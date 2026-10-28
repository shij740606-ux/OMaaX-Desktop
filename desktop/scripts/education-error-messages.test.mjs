import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.location={pathname:'/education/'};
const {reviewErrorMessage}=await import('../education/src/api/review.js');
const {ApiError}=await import('../education/src/api/http.js');
const cases={AUTH_EXPIRED:'登录已失效',MODEL_AUTH:'拒绝了临时凭据',MODEL_FORBIDDEN:'当前账号',
 MODEL_NOT_CONNECTED:'尚未连接',MODEL_REQUEST_INVALID:'未接受审稿请求',MODEL_INVALID_RESPONSE:'格式异常',
 INPUT_TOO_LONG:'论文内容超出',OUTPUT_TRUNCATED:'报告不完整',RATE_LIMIT:'过于频繁',TIMEOUT:'超时',NETWORK:'网络异常'};
for(const [code,expected] of Object.entries(cases)) test(`education error ${code}`,()=>{
 const message=reviewErrorMessage(new ApiError('private detail',{code}));
 assert.ok(message.includes(expected));assert.ok(!message.includes('private detail'));
});
