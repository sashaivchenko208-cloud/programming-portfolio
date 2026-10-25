"""MAX transport helpers, not a running chatbot. No network calls on import."""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class MaxClient:
    def __init__(self, token, base='https://platform-api2.max.ru', timeout=10):
        if not token: raise ValueError('MAX token is required')
        self.token,self.base,self.timeout=token,base.rstrip('/'),timeout
    def _request(self,method,path,params,payload=None):
        url=self.base+path+'?'+urlencode(params)
        data=None if payload is None else json.dumps(payload).encode()
        req=Request(url,data=data,method=method,headers={'Authorization':self.token,'Content-Type':'application/json'})
        # Network/auth errors propagate; never interpret a failure as confirmed subscription.
        with urlopen(req,timeout=self.timeout) as response:
            return json.load(response)
    def subscribed(self,user_id,chat_id):
        result=self._request('GET',f'/chats/{int(chat_id)}/members',{'user_ids':int(user_id)})
        if not isinstance(result.get('members'),list): raise ValueError('Unexpected MAX members response')
        return any(str(m.get('user_id'))==str(user_id) for m in result['members'])
    def send(self,user_id,text):
        return self._request('POST','/messages',{'user_id':int(user_id)},{'text':text,'notify':True})
