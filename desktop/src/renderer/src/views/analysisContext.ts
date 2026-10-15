export type AnalysisContext={projectId:string;projectName:string;sessionId:string;sessionName:string};
export const analysisContext=(sessionId:string|null)=>window.biodsh.dshRpc('server.context',sessionId?{sessionId}:{}) as Promise<AnalysisContext>;
export function openAnalysisFiles(sessionId:string|null,serverId?:string){
 sessionStorage.setItem('omaax.files.open',JSON.stringify({sessionId,serverId}));
}
