"""Exercise real HTTP admin traffic over SSH, including multipart media upload."""
import hashlib
import http.client
from http.cookies import SimpleCookie
import io
import json
import re
import urllib.parse
from PIL import Image
from remote import LOCAL

def verify_admin(client):
    cookies=SimpleCookie()
    def request(method,path,body=None,ctype=None):
        sock=client.get_transport().open_channel('direct-tcpip',('127.0.0.1',80),('127.0.0.1',0))
        conn=http.client.HTTPConnection('127.0.0.1',timeout=20)
        conn.sock=sock
        headers={'Host':'127.0.0.1','Connection':'close'}
        if cookies:headers['Cookie']='; '.join(k+'='+v.value for k,v in cookies.items())
        if ctype:headers['Content-Type']=ctype
        conn.request(method,path,body,headers)
        response=conn.getresponse()
        for name,value in response.getheaders():
            if name.lower()=='set-cookie':cookies.load(value)
        data=response.read()
        code=response.status
        conn.close()
        return code,data
    def csrf(data):
        return re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"',data).group(1).decode()
    code,data=request('GET','/admin/login/?next=/admin/')
    assert code==200
    password=json.loads((LOCAL/'app-secrets.json').read_text())['db_password']
    payload=urllib.parse.urlencode({'csrfmiddlewaretoken':csrf(data),'username':'dz05-check',
                                   'password':password,'next':'/admin/'}).encode()
    code,data=request('POST','/admin/login/?next=/admin/',payload,'application/x-www-form-urlencoded')
    assert code==302,'Admin login failed'
    code,data=request('GET','/admin/knowledge/article/add/')
    assert code==200
    token=csrf(data)
    image=io.BytesIO()
    Image.new('RGB',(8,8),'blue').save(image,format='PNG')
    png=image.getvalue()
    boundary='dz05MultipartProofBoundary'
    body=bytearray()
    fields={'csrfmiddlewaretoken':token,'title':'Проверка загрузки ДЗ05','slug':'dz05-media-proof',
            'lesson':'5','summary':'Real HTTP write','body':'committed via admin HTTP','published':'on','_save':'Сохранить'}
    for name,value in fields.items():
        body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+value+'\r\n').encode())
    body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="image"; filename="proof.png"\r\nContent-Type: image/png\r\n\r\n').encode())
    body.extend(png)
    body.extend(('\r\n--'+boundary+'--\r\n').encode())
    code,data=request('POST','/admin/knowledge/article/add/',bytes(body),'multipart/form-data; boundary='+boundary)
    assert code==302,'Article creation failed'
    code,data=request('GET','/articles/dz05-media-proof/')
    assert code==200
    path=re.search(rb'src="(/media/[^"]+)"',data).group(1).decode()
    code,data=request('GET',path)
    assert code==200 and data==png,'Uploaded media round-trip failed'
    return {'transport':'HTTP over trusted SSH tunnel','login_status':302,'article_create_status':302,
            'article_read_status':200,'media_read_status':code,'media_sha256':hashlib.sha256(data).hexdigest()}
