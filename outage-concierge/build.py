# Injects app_data.json into template.html -> index.html (self-contained, open in any browser)
d=open('app_data.json').read(); t=open('template.html',encoding='utf8').read()
open('index.html','w',encoding='utf8').write(t.replace('/*DATA*/',d)); print('built index.html')
