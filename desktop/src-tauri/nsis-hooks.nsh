; Bind existing shortcuts to a standalone icon after changing the installation path.
; Preserve the installer's no-shortcut option, AppUserModelID and unrelated links.
!define OMAAX_HOOK_DIR "${__FILEDIR__}"
Var OMaaXIcon
Var OMaaXMainExe
Var OMaaXDesktopLink
Var OMaaXStartMenuLink
!macro NSIS_HOOK_PREINSTALL
  InitPluginsDir
  File /oname=$PLUGINSDIR\omaax-stop-agent.ps1 "${OMAAX_HOOK_DIR}\..\scripts\stop-installed-agent.ps1"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\omaax-stop-agent.ps1" -InstallDir "$INSTDIR"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Please close OMaaX and retry the installation. / 请关闭 OMaaX 后重试安装。" /SD IDOK
    Abort
  ${EndIf}
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
