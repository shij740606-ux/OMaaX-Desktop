; Bind existing shortcuts to a standalone icon after changing the installation path.
; Preserve the installer's no-shortcut option, AppUserModelID and unrelated links.
!define OMAAX_HOOK_DIR "${__FILEDIR__}"
; A skipped file must never result in an apparently successful mixed-version install.
AllowSkipFiles off
Var OMaaXIcon
Var OMaaXMainExe
Var OMaaXDesktopLink
Var OMaaXStartMenuLink
Var OMaaXCopyStarted
Var OMaaXUninstKey
Var OMaaXDisplayName

Function OMaaXMarkInstallFailed
  ${If} $OMaaXCopyStarted = 1
    DeleteRegValue SHCTX "$OMaaXUninstKey" "DisplayVersion"
    WriteRegStr SHCTX "$OMaaXUninstKey" "DisplayName" "$OMaaXDisplayName (安装未完成 / incomplete)"
    WriteRegStr SHCTX "$OMaaXUninstKey" "InstallState" "failed"
    Delete "$INSTDIR\installation\verified.json"
  ${EndIf}
FunctionEnd

Function .onInstFailed
  Call OMaaXMarkInstallFailed
  SetErrorLevel 2
FunctionEnd

!macro NSIS_HOOK_PREINSTALL
  InitPluginsDir
  File /oname=$PLUGINSDIR\omaax-stop-agent.ps1 "${OMAAX_HOOK_DIR}\..\scripts\stop-installed-agent.ps1"
  File /oname=$PLUGINSDIR\omaax-payload.json "${OMAAX_HOOK_DIR}\..\installer-payload\manifest.json"
  nsExec::ExecToStack /TIMEOUT=300000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\omaax-stop-agent.ps1" -InstallDir "$INSTDIR" -Manifest "$PLUGINSDIR\omaax-payload.json"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    DetailPrint "$1"
    MessageBox MB_OK|MB_ICONSTOP "无法替换安装文件，请关闭 OMaaX 后重试；如仍失败，请查看安装详情。 / Cannot replace installation files. Close OMaaX and retry. See installation details for the affected path." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  StrCpy $OMaaXUninstKey "${UNINSTKEY}"
  StrCpy $OMaaXDisplayName "${PRODUCTNAME}"
  StrCpy $OMaaXCopyStarted 1
  Delete "$INSTDIR\installation\verified.json"
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallState" "pending"
!macroend

!macro OMaaXBindShortcutIcon shortcut
  ${If} ${FileExists} "$OMaaXIcon"
  ${AndIf} ${FileExists} "${shortcut}"
  !insertmacro IsShortcutTarget "${shortcut}" "$OMaaXMainExe"
  Pop $3
  ${If} $3 = 1
    !insertmacro ComHlpr_CreateInProcInstance ${CLSID_ShellLink} ${IID_IShellLink} r0 ""
    ${If} $0 P<> 0
      ${IUnknown::QueryInterface} $0 '("${IID_IPersistFile}",.r1)'
      ${If} $1 P<> 0
        ${IPersistFile::Load} $1 '("${shortcut}", ${STGM_READWRITE})'
        ${IShellLink::SetIconLocation} $0 '(w "$OMaaXIcon", 0)'
        ${IPersistFile::Save} $1 '("${shortcut}",1)'
        ${IUnknown::Release} $1 ""
      ${EndIf}
      ${IUnknown::Release} $0 ""
    ${EndIf}
  ${EndIf}
  ${EndIf}
!macroend

Function OMaaXRefreshShortcutIcons
  !insertmacro OMaaXBindShortcutIcon "$OMaaXDesktopLink"
  !insertmacro OMaaXBindShortcutIcon "$OMaaXStartMenuLink"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
FunctionEnd

!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "正在核对安装文件 / Verifying installation files..."
  File /oname=$PLUGINSDIR\omaax-verify-payload.ps1 "${OMAAX_HOOK_DIR}\..\scripts\verify-installed-payload.ps1"
  nsExec::ExecToStack /TIMEOUT=300000 '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\omaax-verify-payload.ps1" -InstallDir "$INSTDIR" -Manifest "$PLUGINSDIR\omaax-payload.json" -Receipt "installation\verified.json" -ExpectedVersion "${VERSION}"'
  Pop $0
  Pop $1
  DetailPrint "$1"
  ${If} $0 != 0
    Call OMaaXMarkInstallFailed
    MessageBox MB_OK|MB_ICONSTOP "安装文件校验未通过，升级尚未完成。请重新运行安装包；配置和对话记录会保留。详情：$\r$\n$1" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallState" "complete"
  StrCpy $OMaaXCopyStarted 0
  StrCpy $OMaaXIcon "$INSTDIR\omaax-desktop-${VERSION}.ico"
  StrCpy $OMaaXMainExe "$INSTDIR\${MAINBINARYNAME}.exe"
  StrCpy $OMaaXDesktopLink "$DESKTOP\${PRODUCTNAME}.lnk"
  !if "${STARTMENUFOLDER}" != ""
    StrCpy $OMaaXStartMenuLink "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
  !else
    StrCpy $OMaaXStartMenuLink "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  !endif
  Call OMaaXRefreshShortcutIcons
!macroend

; Interactive Finish creates the desktop shortcut AFTER POSTINSTALL. Silent
; installs use POSTINSTALL; GUI installs also bind after Finish has run.
Function .onGUIEnd
  Call OMaaXRefreshShortcutIcons
FunctionEnd
