// Exercise the actual Business Forms submit code with mocked browser/API services.
import assert from 'node:assert/strict';
import fs from 'node:fs';
const code = fs.readFileSync(new URL('../deploy/grafana/setpoint-submit.js', import.meta.url), 'utf8');
const execute = new Function('context', 'fetch', code);

async function scenario({http=200, body={status:'applied', setpoint_c:32.12, device_id:'test'},
                         value=32.123,
                         lookupHttp=200, source={uid:'control-test',type:'marcusolsson-json-datasource',access:'proxy'}, networkError=false, formAsFunction=false}={}) {
  const values = {setpoint_c:value, status:'none'};
  const requests = [], success = [], errors = [];
  let enabled=true, refresh=0;
  const context = {
    panel:{formValue:formAsFunction ? ()=>({...values}) : {...values}, patchFormValue:p=>Object.assign(values,p),
           disableSubmit:()=>{enabled=false;}, enableSubmit:()=>{enabled=true;}},
    grafana:{notifySuccess:x=>success.push(x),
             notifyError:x=>errors.push(x), refresh:()=>{refresh++;}}
  };
  await execute(context, async (url, options)=>{
    requests.push({url,options});
    if(networkError) throw new Error('Failed to fetch');
    if (options.method === 'GET') return {ok:lookupHttp===200, status:lookupHttp, json:async()=>source};
    return {ok:http===200, status:http, json:async()=>body};
  });
  return {values,requests,success,errors,enabled,refresh};
}

const ok=await scenario();
assert.equal((await scenario({formAsFunction:true})).success.length,1);
assert.equal(ok.success.length,1);
assert.equal(ok.refresh,1);
assert.equal(ok.enabled,true);
assert.equal('Authorization' in ok.requests[1].options.headers,false);
assert.deepEqual(JSON.parse(ok.requests[1].options.body),{setpoint_c:32.12});

assert.equal(ok.requests[0].url,'/api/datasources/name/airheater-control-api');
assert.equal(ok.requests[1].url,'/api/datasources/proxy/uid/control-test/api/setpoint');
for (const request of ok.requests) {
  assert.ok(request.url.startsWith('/api/datasources/'));
  assert.equal(request.options.credentials,'same-origin');
}
assert.equal(ok.requests.filter(r=>r.options.method==='POST').length,1);
assert.match(ok.values.status,/32.12/);
for(const http of [403,409,429,502,503,504]) {
  const r=await scenario({http,body:{status:'error'}});
  assert.equal(r.success.length,0);
  assert.equal(r.errors.length,1);
  assert.equal(r.enabled,true);
}
for(const value of [24,37,NaN,Infinity]) {
  const r=await scenario({value});
  assert.equal(r.requests.length,0);
  assert.equal(r.errors.length,1);
}
for (const config of [{lookupHttp:404}, {source:{uid:'x',type:'postgres',access:'proxy'}}, {source:{type:'marcusolsson-json-datasource',access:'proxy'}}]) {
  const r=await scenario(config);
  assert.equal(r.requests.length,1);
  assert.equal(r.errors.length,1);
  assert.equal(r.enabled,true);
}
const network=await scenario({networkError:true});
assert.equal(network.errors.length,1);
assert.equal(network.success.length,0);
assert.equal(network.enabled,true);
console.log('Form checks passed: confirmation, failures, validation, keyless submission and API payload');
