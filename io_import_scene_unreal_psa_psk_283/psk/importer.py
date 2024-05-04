import bpy
import os
from mathutils import Vector, Matrix, Quaternion
from utils.data import Vector3, Quaternion, Section
from utils.helpers import rgb_to_srgb, util_bytes_to_str, util_gen_name_part
from utils.types import PskImportOptions
from struct import unpack, unpack_from, Struct

def pskimport(filepath, context, **kwargs):
    # Unpack the kwargs
    bImportmesh = kwargs.get('bImportmesh', True)
    bImportbone = kwargs.get('bImportbone', True)
    bSplitUVdata = kwargs.get('bSpltiUVdata', False)
    fBonesize = kwargs.get('fBonesize', 5.0)
    fBonesizeRatio = kwargs.get('fBonesizeRatio', 0.6)
    bDontInvertRoot = kwargs.get('bDontInvertRoot', True)
    bReorientBones = kwargs.get('bReorientBones', False)
    bReorientDirectly = kwargs.get('bReorientDirectly', False)
    bScaleDown = kwargs.get('bScaleDown', True)
    bToSRGB = kwargs.get('bToSRGB', True)
    bSmoothShade = kwargs.get('bSmoothShade', True)
    error_callback = kwargs.get('error_callback', print)

    # Read the .psk file
    try:
        with open(filepath, 'rb') as file:
            header = Section()
            file.readinto(header)
            if header.type_flags != 1999801:
                error_callback(f"File {filepath} is not a valid PSK file.")
                return False

            # Read the different sections of the PSK file
            vertices, wedges, faces, uv_by_face, materials, bones, weights, vertex_colors, extrauvs, normals, wedge_idx_by_face_idx, morph_infos, morph_deltas = read_psk_data(file, header.data_count, bImportmesh, bImportbone, bScaleDown)
    except IOError:
        error_callback(f"Error while opening file for reading: {filepath}")
        return False

    # Build the mesh and armature
    mesh_data = bpy.data.meshes.new(f"{os.path.splitext(os.path.basename(filepath))[0]}_mesh")
    mesh_obj = bpy.data.objects.new(f"{os.path.splitext(os.path.basename(filepath))[0]}_object", mesh_data)
    armature_data = bpy.data.armatures.new(f"{os.path.splitext(os.path.basename(filepath))[0]}_armature")
    armature_obj = bpy.data.objects.new(f"{os.path.splitext(os.path.basename(filepath))[0]}_armature_object", armature_data)

    build_mesh(mesh_data, vertices, faces, uv_by_face, vertex_colors, normals, morph_infos, morph_deltas, bSplitUVdata, bToSRGB, bSmoothShade)
    psk_bones = build_armature(armature_obj, bones, bDontInvertRoot, bReorientBones, bReorientDirectly, fBonesize, fBonesizeRatio)

    # Link the objects to the scene
    context.collection.objects.link(mesh_obj)
    context.collection.objects.link(armature_obj)

    # Parent the mesh to the armature
    mesh_obj.parent = armature_obj
    mesh_obj.parent_type = 'OBJECT'

    # Add the armature modifier to the mesh
    armature_modifier = mesh_obj.modifiers.new(armature_obj.data.name, 'ARMATURE')
    armature_modifier.object = armature_obj
    armature_modifier.use_vertex_groups = True
    armature_modifier.use_bone_envelopes = False

    # Assign vertex weights to the bone vertex groups
    assign_vertex_weights(mesh_obj, psk_bones, weights)

    return True

def read_psk_data(file, chunk_datacount, bImportmesh, bImportbone, bScaleDown):
    vertices = read_vertices(file, chunk_datacount, bScaleDown)
    wedges = read_wedges(file, chunk_datacount, bImportmesh)
    faces, uv_by_face, wedge_idx_by_face_idx = read_faces(file, chunk_datacount, wedges, bImportmesh)
    materials = read_materials(file, chunk_datacount)
    bones, bImportbone = read_bones(file, chunk_datacount, bImportbone)
    weights = read_weights(file, chunk_datacount, bImportmesh)
    vertex_colors = read_vertex_colors(file, chunk_datacount)
    extrauvs = read_extrauvs(file, chunk_datacount)
    normals = read_normals(file, chunk_datacount, bImportmesh)
    morph_infos, morph_deltas = read_morph_data(file, chunk_datacount, bImportmesh)
    return vertices, wedges, faces, uv_by_face, materials, bones, weights, vertex_colors, extrauvs, normals, wedge_idx_by_face_idx, morph_infos, morph_deltas

def read_vertices(file, chunk_datacount, bScaleDown):
    vertices = [None] * chunk_datacount
    unpack_data = Struct('3f').unpack_from
    for counter in range(chunk_datacount):
        vec_x, vec_y, vec_z = unpack_data(file.read(chunk_datacount * 12))
        if bScaleDown:
            vertices[counter] = (vec_x * 0.01, vec_y * 0.01, vec_z * 0.01)
        else:
            vertices[counter] = (vec_x, vec_y, vec_z)
    return vertices

# Similar functions for read_wedges, read_faces, read_materials, read_bones, read_weights, read_vertex_colors, read_extrauvs, read_normals, read_morph_data

def build_mesh(mesh_data, vertices, faces, uv_by_face, vertex_colors, normals, morph_infos, morph_deltas, bSplitUVdata, bToSRGB, bSmoothShade):
    # Build the mesh using the read data
    mesh_data.from_pydata(vertices, [], faces)

    if normals is not None:
        mesh_data.polygons.foreach_set("use_smooth", [True] * len(mesh_data.polygons))
        mesh_data.normals_split_custom_set_from_vertices(normals)
        if bpy.app.version < (4, 0, 0):
            mesh_data.use_auto_smooth = True

    if morph_infos is not None:
        add_morph_targets(mesh_data, morph_infos, morph_deltas, bScaleDown)

    add_uv_layers(mesh_data, uv_by_face, bSplitUVdata)
    add_vertex_colors(mesh_data, vertex_colors, bToSRGB)

    if bSmoothShade:
        for face in mesh_data.polygons:
            face.use_smooth = True

def build_armature(armature_obj, bones, bDontInvertRoot, bReorientBones, bReorientDirectly, fBonesize, fBonesizeRatio):
    armature_data = armature_obj.data
    armature_data.display_type = 'STICK'
    armature_obj.show_in_front = True

    psk_bones = [None] * len(bones)
    sum_bone_pos = 0

    for counter, (name_raw, flags, num_children, parent_index, quat_x, quat_y, quat_z, quat_w, vec_x, vec_y, vec_z) in enumerate(bones):
        psk_bone = init_psk_bone(counter, psk_bones, name_raw)
        psk_bone.bone_index = counter
        psk_bone.parent_index = parent_index
        psk_bone.orig_quat = Quaternion((quat_w, quat_x, quat_y, quat_z))
        if bScaleDown:
            psk_bone.orig_loc = Vector((vec_x * 0.01, vec_y * 0.01, vec_z * 0.01))
        else:
            psk_bone.orig_loc = Vector((vec_x, vec_y, vec_z))
        sum_bone_pos += psk_bone.orig_loc.length

    avg_bone_len = sum_bone_pos / len(bones)
    avg_bone_len *= fBonesizeRatio
    bone_size_chosen = max(0.01, round(min(avg_bone_len, fBonesize) * 100) / 100)

    utils_set_mode('EDIT')
    for psk_bone in psk_bones:
        edit_bone = armature_data.edit_bones.new(psk_bone.name)
        if psk_bone.parent is not None:
            edit_bone.parent = armature_data.edit_bones[psk_bone.parent.name]
        else:
            if bDontInvertRoot:
                psk_bone.orig_quat.conjugate()
        if bReorientBones:
            bone_len, quat_orient_diff = calc_bone_rotation(psk_bone, bone_size_chosen, bReorientDirectly, avg_bone_len)
            post_quat = quat_orient_diff
            post_quat.rotate(psk_bone.orig_quat.conjugated())
        else:
            post_quat = psk_bone.orig_quat.conjugated()
        edit_bone.tail = Vector((0.0, bone_size_chosen, 0.0))
        m = post_quat.copy()
        m.rotate(psk_bone.mat_world)
        m = m.to_matrix().to_4x4()
        m.translation = psk_bone.mat_world.translation
        edit_bone.matrix = m
        edit_bone["orig_quat"] = psk_bone.orig_quat
        edit_bone["orig_loc"] = psk_bone.orig_loc
        edit_bone["post_quat"] = post_quat
    utils_set_mode('OBJECT')
    return psk_bones

# Helper functions like init_psk_bone, calc_bone_rotation, utils_set_mode, etc.

def assign_vertex_weights(mesh_obj, psk_bones, weights):
    for psk_bone in psk_bones:
        if psk_bone.have_weight_data:
            psk_bone.vertex_group = mesh_obj.vertex_groups.new(name=psk_bone.name)
            for weight, vertex_id, bone_index in filter(None, weights):
                if vertex_id < len(vertices):
                    psk_bone.vertex_group.add([vertex_id], weight, 'ADD')

def add_uv_layers(mesh_data, uv_by_face, bSplitUVdata):
    if bSplitUVdata:
        for i in range(len(uv_mat_ids)):
            mesh_data.uv_layers.new(name=f"UV{i}")
    else:
        mesh_data.uv_layers.new(name="UV_SINGLE")

    uv_layers = mesh_data.uv_layers
    for faceIdx, (faceUVs, faceMatIdx, WedgeMatIds) in enumerate(uv_by_face):
        for vertN, uv in enumerate(faceUVs):
            loopId = faceIdx * 3 + vertN
            if bSplitUVdata:
                uv_layers[WedgeMatIds[vertN]].data[loopId].uv = uv
            else:
                uv_layers[0].data[loopId].uv = uv

def add_vertex_colors(mesh_data, vertex_colors, bToSRGB):
    vtx_color_layer = mesh_data.vertex_colors.new(name="PSKVTXCOL_0", do_init=False)
    pervertex = [None] * len(vertices)
    for counter, (vertexid, _, _, _) in enumerate(wedges):
        pervertex[vertexid] = vertex_colors[counter]

    for counter, loop in enumerate(mesh_data.loops):
        color = pervertex[loop.vertex_index]
        if color is None:
            vtx_color_layer.data[counter].color = (1., 1., 1., 1.)
        else:
            if bToSRGB:
                vtx_color_layer.data[counter].color = (
                    rgb_to_srgb(color[0] / 255),
                    rgb_to_srgb(color[1] / 255),
                    rgb_to_srgb(color[2] / 255),
                    color[3] / 255
                )
            else:
                vtx_color_layer.data[counter].color = (
                    color[0] / 255,
                    color[1] / 255,
                    color[2] / 255,
                    color[3] / 255
                )

def add_morph_targets(mesh_data, morph_infos, morph_deltas, bScaleDown):
    default_key = mesh_obj.shape_key_add(name="Default", from_mix=False)
    default_key.interpolation = 'KEY_LINEAR'
    morph_data_position = 0
    scale = 0.01 if bScaleDown else 1.00

    for (morph_name, vertex_count) in morph_infos:
        key = mesh_obj.shape_key_add(name=util_bytes_to_str(morph_name), from_mix=False)
        key.interpolation = 'KEY_LINEAR'
        for i in range(morph_data_position, morph_data_position + vertex_count):
            pos_x, pos_y, pos_z, norm_x, norm_y, norm_z, index = morph_deltas[i]
            key.data[index].co += Vector((pos_x * scale, -pos_y * scale, pos_z * scale))
        morph_data_position += vertex_count