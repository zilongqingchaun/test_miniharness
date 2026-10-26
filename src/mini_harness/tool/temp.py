from os import walk
import os
from pathlib import Path
path=Path(r'D:\code\project\my_agent\test_miniharness\src\mini_harness')
for root,dirs,files in os.walk(path):
    print(root)
    print(dirs)
    print(files)