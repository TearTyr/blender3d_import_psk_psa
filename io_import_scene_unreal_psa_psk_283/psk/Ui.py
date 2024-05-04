# UI.py
import bpy

class PSKPSA_OT_show_message(bpy.types.Operator):
    bl_idname = "pskpsa.message"
    bl_label = "PSA/PSK"
    bl_options = {'REGISTER', 'INTERNAL'}

    message : StringProperty(default = 'Message')

    lines = []
    line0 = None
    def execute(self, context):
        self.lines = self.message.split("\n")
        maxlen = 0
        for line in self.lines:
            if len(line) > maxlen:
                maxlen = len(line)
                
        print(self.message)
            
        self.report({'WARNING'}, self.message)
        return {'FINISHED'}
        
    def invoke(self, context, event):
        self.lines = self.message.split("\n")
        maxlen = 0
        for line in self.lines:
            if len(line) > maxlen:
                maxlen = len(line)
                
        self.line0 = self.lines.pop(0)
        
        return context.window_manager.invoke_props_dialog(self, width = 100 + 6*maxlen)
      
    def cancel(self, context):
        # print('cancel')
        self.execute(self)
        
    def draw(self, context):
        layout = self.layout
        sub = layout.column()
        sub.label(text = self.line0, icon = 'ERROR')

        for line in self.lines:
            sub.label(text = line)