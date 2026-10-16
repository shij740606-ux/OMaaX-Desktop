export const useApp=()=>({currentSession:'qa-session',setTab:(tab:string)=>{(window as any).__qa.navigation=tab;}});
