from ctypes import Structure, sizeof, c_char, c_int32, c_float
from collections import OrderedDict
from typing import Type, List, Optional, Tuple

import bpy
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty
from bpy.types import Operator, ExportHelper, Context, Object, Action, AnimData, Armature, Bone, PoseBone

def util_bytes_to_str(in_bytes):
    return in_bytes.rstrip(b'\x00').decode(encoding='cp1252', errors='replace')

def util_get_scene(context):
    return context.scene

def util_obj_select(context, obj, action='SELECT'):
    if obj.name in context.view_layer.objects:
        return obj.select_set(action == 'SELECT')
    else:
        print('Warning: util_obj_select: Object not in "context.view_layer.objects"')

def util_obj_set_active(context, obj):
    context.view_layer.objects.active = obj

def util_select_all(select):
    if select:
        actionString = 'SELECT'
    else:
        actionString = 'DESELECT'

    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action=actionString)

    if bpy.ops.mesh.select_all.poll():
        bpy.ops.mesh.select_all(action=actionString)

    if bpy.ops.pose.select_all.poll():
        bpy.ops.pose.select_all(action=actionString)

def util_ui_show_msg(msg):
    bpy.ops.pskpsa.message('INVOKE_DEFAULT', message=msg)

def blen_get_armature_from_selection():
    armature_obj = None
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE' and obj.select_get():
            armature_obj = obj
            break
    if armature_obj is None:
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.select_get():
                for modifier in obj.modifiers:
                    if modifier.type == 'ARMATURE':
                        armature_obj = modifier.object
                        break
    return armature_obj

class class_psa_bone:
    name = ""
    parent = None
    bone_index = 0
    parent_index = 0
    mat_world = None
    mat_world_rot = None
    orig_quat = None
    orig_loc = None
    children = None
    have_weight_data = False

class Psa:
    class Bone(Structure):
        _fields_ = [
            ('name', c_char * 64),
            ('flags', c_int32),
            ('children_count', c_int32),
            ('parent_index', c_int32),
            ('rotation', Quaternion),
            ('location', Vector3),
            ('padding', c_char * 16)
        ]

    class Sequence(Structure):
        _fields_ = [
            ('name', c_char * 64),
            ('group', c_char * 64),
            ('bone_count', c_int32),
            ('root_include', c_int32),
            ('compression_style', c_int32),
            ('key_quotum', c_int32),
            ('key_reduction', c_float),
            ('track_time', c_float),
            ('fps', c_float),
            ('start_bone', c_int32),
            ('frame_start_index', c_int32),
            ('frame_count', c_int32)
        ]

    class Key(Structure):
        _fields_ = [
            ('location', Vector3),
            ('rotation', Quaternion),
            ('time', c_float)
        ]

        @property
        def data(self):
            yield self.rotation.w
            yield self.rotation.x
            yield self.rotation.y
            yield self.rotation.z
            yield self.location.x
            yield self.location.y
            yield self.location.z

        def __repr__(self) -> str:
            return repr((self.location, self.rotation, self.time))

    def __init__(self):
        self.bones: List[Psa.Bone] = []
        self.sequences: OrderedDict[str, Psa.Sequence] = OrderedDict()
        self.keys: List[Psa.Key] = []

class Section(Structure):
    _fields_ = [
        ('name', c_char * 20),
        ('type_flags', c_int32),
        ('data_size', c_int32),
        ('data_count', c_int32)
    ]

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.type_flags = 1999801

def write_section(fp, name: bytes, data_type: Type[Structure] = None, data: list = None):
    section = Section()
    section.name = name
    if data_type is not None and data is not None:
        section.data_size = sizeof(data_type)
        section.data_count = len(data)
    fp.write(section)
    if data is not None:
        for datum in data:
            fp.write(datum)

def write_psa(psa: Psa, path: str):
    with open(path, 'wb') as fp:
        write_section(fp, b'ANIMHEAD')
        write_section(fp, b'BONENAMES', Psa.Bone, psa.bones)
        write_section(fp, b'ANIMINFO', Psa.Sequence, list(psa.sequences.values()))
        write_section(fp, b'ANIMKEYS', Psa.Key, psa.keys)

def psa_export(filepath,
               context=None,
               oArmature=None,
               bFilenameAsPrefix=False,
               bActionsToTrack=False,
               first_frames=0,
               bDontInvertRoot=True,
               bUpdateTimelineRange=False,
               bRotationOnly=False,
               bScaleDown=True,
               fcurve_interpolation='LINEAR',
               error_callback=util_ui_show_msg,
               actions_to_export=None):
    if not context:
        context = bpy.context

    if not oArmature:
        oArmature = blen_get_armature_from_selection()
        if not oArmature:
            error_callback("No armature selected.")
            return False

    export_sequences = []

    if actions_to_export is None:
        actions_to_export = bpy.data.actions

    for action in actions_to_export:
        if not is_action_for_armature(oArmature.data, action):
            continue

        export_sequence = PsaBuildSequence()
        export_sequence.nla_state.action = action
        export_sequence.name = action.name
        export_sequence.nla_state.frame_start = int(action.frame_range[0])
        export_sequence.nla_state.frame_end = int(action.frame_range[1])
        export_sequence.fps = context.scene.render.fps
        export_sequence.compression_ratio = 1.0
        export_sequence.key_quota = 0
        export_sequences.append(export_sequence)

    options = PsaBuildOptions()
    options.animation_data = oArmature.animation_data
    options.sequences = export_sequences
    options.bone_filter_mode = 'ALL'
    options.bone_collection_indices = []
    options.should_enforce_bone_name_restrictions = False
    options.sequence_name_prefix = '' if not bFilenameAsPrefix else util_gen_name_part(filepath) + '_'
    options.sequence_name_suffix = ''
    options.root_motion = not bRotationOnly

    psa = build_psa(context, options)
    write_psa(psa, filepath)

    return True

class EXPORT_OT_psa(Operator, ExportHelper):
    """Export animation data to a .psa file"""
    bl_idname = "export_scene.psa"
    bl_label = "Export PSA"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_options = {'UNDO'}

    filepath: StringProperty(
        subtype='FILE_PATH',
    )
    filter_glob: StringProperty(
        default="*.psa",
        options={'HIDDEN'},
    )
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: bpy.props.StringProperty(subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    def draw(self, context):
        self.draw_psa(context)
        self.layout.prop(context.scene.pskpsa_import, 'bDontInvertRoot')

    def execute(self, context):
        props = context.scene.pskpsa_import
        psa_export(
            self.filepath,
            context=context,
            oArmature=blen_get_armature_from_selection(),
            bFilenameAsPrefix=props.bFilenameAsPrefix,
            bActionsToTrack=props.bActionsToTrack,
            bDontInvertRoot=props.bDontInvertRoot,
            bUpdateTimelineRange=props.bUpdateTimelineRange,
            bRotationOnly=props.bRotationOnly,
            bScaleDown=props.bScaleDown,
            fcurve_interpolation=props.fcurve_interpolation,
            error_callback=util_ui_show_msg,
            actions_to_export=None
        )
        return {'FINISHED'}

    def invoke(self, context, event):
        if blen_get_armature_from_selection() is None:
            util_ui_show_msg('Select an armature.')
            return {'FINISHED'}
        wm = context.window_manager
        wm.fileselect_add(self)
        return {'RUNNING_MODAL'}

def is_action_for_armature(armature: Armature, action: Action):
    if len(action.fcurves) == 0:
        return False
    bone_names = set([x.name for x in armature.bones])
    for fcurve in action.fcurves:
        match = re.match(r'pose\.bones$$\"([^\"]+)\"]($$\"([^\"]+)\"])?', fcurve.data_path)
        if not match:
            continue
        bone_name = match.group(1)
        if bone_name in bone_names:
            return True
    return False

def util_gen_name_part(filepath):
    return re.match(r'.*[/\$$([^/\$$+?)(\..{2,5})?$', filepath).group(1)

class PsaBuildSequence:
    class NlaState:
        def __init__(self):
            self.action: Optional[Action] = None
            self.frame_start: int = 0
            self.frame_end: int = 0

    def __init__(self):
        self.name: str = ''
        self.nla_state: PsaBuildSequence.NlaState = PsaBuildSequence.NlaState()
        self.compression_ratio: float = 1.0
        self.key_quota: int = 0
        self.fps: float = 30.0

class PsaBuildOptions:
    def __init__(self):
        self.animation_data: Optional[AnimData] = None
        self.sequences: List[PsaBuildSequence] = []
        self.bone_filter_mode: str = 'ALL'
        self.bone_collection_indices: List[int] = []
        self.should_enforce_bone_name_restrictions: bool = False
        self.sequence_name_prefix: str = ''
        self.sequence_name_suffix: str = ''
        self.root_motion: bool = False

def _get_pose_bone_location_and_rotation(pose_bone: PoseBone, armature_object: Object, options: PsaBuildOptions):
    if pose_bone.parent is not None:
        pose_bone_matrix = pose_bone.matrix
        pose_bone_parent_matrix = pose_bone.parent.matrix
        pose_bone_matrix = pose_bone_parent_matrix.inverted() @ pose_bone_matrix
    else:
        if options.root_motion:
            # Get the bone's pose matrix, taking the armature object's world matrix into account.
            pose_bone_matrix = armature_object.matrix_world @ pose_bone.matrix
        else:
            # Use the bind pose matrix for the root bone.
            pose_bone_matrix = pose_bone.matrix

    location = pose_bone_matrix.to_translation()
    rotation = pose_bone_matrix.to_quaternion().normalized()

    if pose_bone.parent is not None:
        rotation.conjugate()

    return location, rotation

def build_psa(context: bpy.types.Context, options: PsaBuildOptions) -> Psa:
    active_object = context.view_layer.objects.active

    psa = Psa()

    armature_object = active_object
    armature_data = typing.cast(Armature, armature_object.data)
    bones: List[Bone] = list(iter(armature_data.bones))

    # The order of the armature bones and the pose bones is not guaranteed to be the same.
    # As a result, we need to reconstruct the list of pose bones in the same order as the
    # armature bones.
    bone_names = [x.name for x in bones]
    pose_bones = [(bone_names.index(bone.name), bone) for bone in armature_object.pose.bones]
    pose_bones.sort(key=lambda x: x[0])
    pose_bones = [x[1] for x in pose_bones]

    # Get a list of all the bone indices and instigator bones for the bone filter settings.
    export_bone_names = get_export_bone_names(armature_object, options.bone_filter_mode, options.bone_collection_indices)
    bone_indices = [bone_names.index(x) for x in export_bone_names]

    # Make the bone lists contain only the bones that are going to be exported.
    bones = [bones[bone_index] for bone_index in bone_indices]
    pose_bones = [pose_bones[bone_index] for bone_index in bone_indices]

    # No bones are going to be exported.
    if len(bones) == 0:
        raise RuntimeError('No bones available for export')

    # Check that all bone names are valid.
    if options.should_enforce_bone_name_restrictions:
        check_bone_names(map(lambda bone: bone.name, bones))
        
    # Build list of PSA bones.
    for bone in bones:
        psa_bone = Psa.Bone()

        try:
            psa_bone.name = bytes(bone.name, encoding='windows-1252')
        except UnicodeEncodeError:
            raise RuntimeError(f'Bone name "{bone.name}" contains characters that cannot be encoded in the Windows-1252 codepage')

        try:
            parent_index = bones.index(bone.parent)
            psa_bone.parent_index = parent_index
            psa.bones[parent_index].children_count += 1
        except ValueError:
            psa_bone.parent_index = 0

        if bone.parent is not None:
            rotation = bone.matrix.to_quaternion().conjugated()
            inverse_parent_rotation = bone.parent.matrix.to_quaternion().inverted()
            parent_head = inverse_parent_rotation @ bone.parent.head
            parent_tail = inverse_parent_rotation @ bone.parent.tail
            location = (parent_tail - parent_head) + bone.head
        else:
            armature_local_matrix = armature_object.matrix_local
            location = armature_local_matrix @ bone.head
            bone_rotation = bone.matrix.to_quaternion().conjugated()
            local_rotation = armature_local_matrix.to_3x3().to_quaternion().conjugated()
            rotation = bone_rotation @ local_rotation
            rotation.conjugate()

        psa_bone.location.x = location.x
        psa_bone.location.y = location.y
        psa_bone.location.z = location.z

        psa_bone.rotation.x = rotation.x
        psa_bone.rotation.y = rotation.y
        psa_bone.rotation.z = rotation.z
        psa_bone.rotation.w = rotation.w

        psa.bones.append(psa_bone)

    # Add prefixes and suffices to the names of the export sequences and strip whitespace.
    for export_sequence in options.sequences:
        export_sequence.name = f'{options.sequence_name_prefix}{export_sequence.name}{options.sequence_name_suffix}'
        export_sequence.name = export_sequence.name.strip()

    # Save the current action and frame so that we can restore the state once we are done.
    saved_frame_current = context.scene.frame_current
    saved_action = options.animation_data.action

    # Now build the PSA sequences.
    # We actually alter the timeline frame and simply record the resultant pose bone matrices.
    frame_start_index = 0

    context.window_manager.progress_begin(0, len(options.sequences))

    for export_sequence_index, export_sequence in enumerate(options.sequences):
        # Link the action to the animation data and update view layer.
        options.animation_data.action = export_sequence.nla_state.action
        context.view_layer.update()

        frame_start = export_sequence.nla_state.frame_start
        frame_end = export_sequence.nla_state.frame_end

        # Calculate the frame step based on the compression factor.
        frame_extents = abs(frame_end - frame_start)
        frame_count_raw = frame_extents + 1
        frame_count = max(export_sequence.key_quota, int(frame_count_raw * export_sequence.compression_ratio))

        try:
            frame_step = frame_extents / (frame_count - 1)
        except ZeroDivisionError:
            frame_step = 0.0

        sequence_duration = frame_count_raw / export_sequence.fps

        # If this is a reverse sequence, we need to reverse the frame step.
        if frame_start > frame_end:
            frame_step = -frame_step

        psa_sequence = Psa.Sequence()
        try:
            psa_sequence.name = bytes(export_sequence.name, encoding='windows-1252')
        except UnicodeEncodeError:
            raise RuntimeError(f'Sequence name "{export_sequence.name}" contains characters that cannot be encoded in the Windows-1252 codepage')
        psa_sequence.frame_count = frame_count
        psa_sequence.frame_start_index = frame_start_index
        psa_sequence.fps = frame_count / sequence_duration
        psa_sequence.bone_count = len(pose_bones)
        psa_sequence.track_time = frame_count
        psa_sequence.key_reduction = 1.0

        frame = float(frame_start)

        for _ in range(frame_count):
            context.scene.frame_set(frame=int(frame), subframe=frame % 1.0)

            for pose_bone in pose_bones:
                location, rotation = _get_pose_bone_location_and_rotation(pose_bone, armature_object, options)

                key = Psa.Key()
                key.location.x = location.x
                key.location.y = location.y
                key.location.z = location.z
                key.rotation.x = rotation.x
                key.rotation.y = rotation.y
                key.rotation.z = rotation.z
                key.rotation.w = rotation.w
                key.time = 1.0 / psa_sequence.fps
                psa.keys.append(key)

            frame += frame_step

        frame_start_index += frame_count

        psa.sequences[export_sequence.name] = psa_sequence

        context.window_manager.progress_update(export_sequence_index)

    # Restore the previous action & frame.
    options.animation_data.action = saved_action
    context.scene.frame_set(saved_frame_current)

    context.window_manager.progress_end()

    return psa