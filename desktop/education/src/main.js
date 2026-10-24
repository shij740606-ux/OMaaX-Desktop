import {createApp} from 'vue';
import {createPinia} from 'pinia';
import EduWorkspace from './components/edu/EduWorkspace.vue';
import {applyHostPreferences, setEducationLocale} from './i18n.js';
import './styles/tokens.css';
import './styles/base.css';
import './styles/animations.css';
import './styles/desktop.css';
setEducationLocale(new URLSearchParams(location.search).get('language') || 'zh');
createApp(EduWorkspace).use(createPinia()).mount('#app');
window.addEventListener('message', applyHostPreferences);
// The host replies after this listener is installed. Switching locale does not
// reload the iframe, so uploads, requests, drafts, and cached panels stay alive.
parent.postMessage({type:'omaax-preferences-ready'}, '*');
// External source links open outside the embedded workspace, never as privileged app navigation.
document.addEventListener('click',event=>{
  const a=event.target.closest('a');if(!a)return;
  const url=new URL(a.href,location.href);
  if(url.origin!==location.origin && ['http:','https:'].includes(url.protocol)){event.preventDefault();parent.postMessage({type:'omaax-external',url:url.href},'*');}
});
