// Exercise the actual Business Forms submit code with mocked browser/API services.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const code = fs.readFileSync(new URL('../deploy/grafana/setpoint-submit.js', import.meta.url), 'utf8');
const execute = new Function('context', 'fetch', code);

async function scenario({http=200, body={status:'applied', setpoint_c:32.12, device_id:'test'},
                         value=32.123, token='test-only-not-a-real-secret-token-123456',
                         api='https://control.example', networkError=false}={}) {
  const values = {setpoint_c:value, operator_token:token, status:'none'};
  const requests = [], success = [], errors = [];
  let enabled=true, refresh=0;
  const context = {
    panel:{formValue:()=>({...values}), patchFormValue:p=>Object.assign(values,p),
           disableSubmit:()=>{enabled=false;}, enableSubmit:()=>{enabled=true;}},
    grafana:{templateService:{replace:()=>api}, notifySuccess:x=>success.push(x),
             notifyError:x=>errors.push(x), refresh:()=>{refresh++;}}
  };
  await execute(context, async (url, options)=>{
    requests.push({url,options});
    if(networkError) throw new Error('Failed to fetch');
    return {ok:http===200, status:http, json:async()=>body};
  });
  return {values,requests,success,errors,enabled,refresh};
}

const ok=await scenario();
assert.equal(ok.success.length,1);
assert.equal(ok.refresh,1);
assert.equal(ok.enabled,true);
assert.equal(ok.values.operator_token,'');
assert.deepEqual(JSON.parse(ok.requests[0].options.body),{setpoint_c:32.12});
assert.match(ok.requests[0].options.headers.Authorization,/^Bearer /);
assert.equal(ok.requests[0].url,'https://control.example/api/setpoint');
assert.match(ok.values.status,/32.12/);
for(const http of [401,403,409,429,502,503,504]) {
  const r=await scenario({http,body:{status:'error'}});
  assert.equal(r.success.length,0);
  assert.equal(r.errors.length,1);
  assert.equal(r.values.operator_token,'');
  assert.equal(r.enabled,true);
}
for(const value of [24,37,NaN,Infinity]) {
  const r=await scenario({value});
  assert.equal(r.requests.length,0);
  assert.equal(r.errors.length,1);
}
for(const api of ['http://control.example','https://control-api.invalid']) {
  const r=await scenario({api});
  assert.equal(r.requests.length,0);
}
const network=await scenario({networkError:true});
assert.equal(network.errors.length,1);
assert.equal(network.success.length,0);
assert.equal(network.enabled,true);
console.log('Form checks passed: confirmation, failures, validation, credential clearing and API payload');
