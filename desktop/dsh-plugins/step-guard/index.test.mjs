import {test} from 'node:test';
import assert from 'node:assert/strict';
import {apply} from './index.js';
test('ordinary, resumed and workflow chats never register artificial stop reminders',()=>{
 const hooks=[];
 apply({on:(...args)=>hooks.push(args)});
 assert.deepEqual(hooks,[]);
});
