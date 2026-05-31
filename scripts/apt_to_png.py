import sys, numpy as np
from PIL import Image
rows={}
for l in open(sys.argv[1]):
    if l.startswith("ROW"):
        p=l.split(); rows[int(p[1])]=[int(x) for x in p[2:]]
nl=max(rows)+1; w=len(rows[0])
img=np.zeros((nl,w),np.uint8)
for i in range(nl): img[i]=rows[i]
Image.fromarray(img,'L').save(sys.argv[2]); print("wrote",sys.argv[2],img.shape)
