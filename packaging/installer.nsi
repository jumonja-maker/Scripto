Unicode true
RequestExecutionLevel user
SetCompressor zlib
CRCCheck force
Name "ScriptoLib"
Caption "Установка ScriptoLib"
OutFile "${OUTPUT_FILE}"
InstallDir "$EXEDIR"
ShowInstDetails show
AutoCloseWindow false
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "ScriptoLib"
VIAddVersionKey "FileDescription" "Установщик ScriptoLib"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "See bundled license notices"
LoadLanguageFile "${NSISDIR}/Contrib/Language files/Russian.nlf"

; This internal engine is started only by setup.c after destination selection.
; The native parent redirects TEMP/TMP to $INSTDIR/cache/setup before process
; creation, so even NSIS's startup temporary-directory probe stays inside it.
; No plugins, registry, shell integration, shortcuts or solid-compression cache.
Page instfiles

Function .onInit
  ReadEnvStr $0 "SCRIPTO_SETUP_ROOT"
  StrCmp $0 "" bad
  StrCmp $0 $INSTDIR 0 bad
  IfFileExists "$INSTDIR\.scriptolib-install.json" bad
  IfFileExists "$INSTDIR\.scriptolib-setup.json" ready bad
  bad:
    MessageBox MB_OK|MB_ICONEXCLAMATION "Запустите основной установщик ScriptoLib-Setup.exe и выберите пустую папку."
    Abort
  ready:
FunctionEnd

Section "ScriptoLib"
  SetOutPath "$INSTDIR"
  SetOverwrite off
  File /r "${PAYLOAD_DIR}/*"
  CreateDirectory "$INSTDIR\data"
  CreateDirectory "$INSTDIR\cache"
  CreateDirectory "$INSTDIR\media"
  DetailPrint "Готово. Запускайте ScriptoLib.exe из выбранной папки."
  DetailPrint "Для удаления используйте Uninstall.exe. Папка media сохранится."
SectionEnd
