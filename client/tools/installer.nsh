!macro preInit
  ; NSIS' old System plugin can crash SHGetKnownFolderPath on recent Windows.
  ; Supply the per-user default using NSIS' resolved LOCALAPPDATA instead.
  ; Existing registrations and the installer's /D or directory choice still win.
  ReadRegStr $0 HKCU "Software\${APP_GUID}" "InstallLocation"
  ${If} $0 == ""
    WriteRegStr HKCU "Software\${APP_GUID}" "InstallLocation" "$LOCALAPPDATA\Programs\${APP_FILENAME}"
  ${EndIf}
!macroend

!macro customInit
  ${If} ${FileExists} "$INSTDIR\${APP_FILENAME}.exe"
    InitPluginsDir
    File /oname=$PLUGINSDIR\checkpoint.cjs "${PROJECT_DIR}\tools\installer-checkpoint.cjs"
    File /oname=$PLUGINSDIR\update-transaction.js "${PROJECT_DIR}\electron\update-transaction.js"
    File /oname=$PLUGINSDIR\build-contract.js "${PROJECT_DIR}\electron\build-contract.js"
    File /oname=$PLUGINSDIR\target-build.json "${PROJECT_DIR}\build-info.json"
    System::Call 'kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", t "1")i.r0'
    nsExec::ExecToStack '"$INSTDIR\${APP_FILENAME}.exe" "$PLUGINSDIR\checkpoint.cjs" "$INSTDIR" "$PLUGINSDIR\target-build.json"'
    Pop $0
    Pop $1
    System::Call 'kernel32::SetEnvironmentVariable(t "ELECTRON_RUN_AS_NODE", t "")i.r1'
    ${If} $0 != 0
      MessageBox MB_ICONSTOP|MB_OK "Could not verify previous Vigil backup. Update cancelled." /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
  ${EndIf}
!macroend
