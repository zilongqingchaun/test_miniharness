tool_manager的问题：
def mark(self,file_path:str,level:str):没有在self.files={}更新结果
def gate(self,func_name:str,args)->ToolItem|None: