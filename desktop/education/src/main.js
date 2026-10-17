import {createApp} from 'vue';
import {createPinia} from 'pinia';
import EduWorkspace from './components/edu/EduWorkspace.vue';
import './styles/tokens.css';
import './styles/base.css';
import './styles/animations.css';
import './styles/desktop.css';
createApp(EduWorkspace).use(createPinia()).mount('#app');
window.addEventListener('message',event=>{
  if(event.source===parent && event.data?.type==='omaax-theme' && ['light','dark'].includes(event.data.theme))document.documentElement.dataset.theme=event.data.theme;
});
// External source links open outside the embedded workspace, never as privileged app navigation.
document.addEventListener('click',event=>{
  const a=event.target.closest('a');if(!a)return;
  const url=new URL(a.href,location.href);
  if(url.origin!==location.origin && ['http:','https:'].includes(url.protocol)){event.preventDefault();parent.postMessage({type:'omaax-external',url:url.href},'*');}
});
