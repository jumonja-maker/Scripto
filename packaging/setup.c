#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <wchar.h>
#include <stdio.h>

#ifndef MAX_INSTALL_ROOT
#define MAX_INSTALL_ROOT 90
#endif

/* Select the installation root BEFORE creating any installer-owned files. */
static wchar_t root[MAX_PATH], engine[MAX_PATH], scratch[MAX_PATH], marker[MAX_PATH];
static int empty_directory(const wchar_t *directory) {
    wchar_t pattern[MAX_PATH];
    swprintf(pattern,MAX_PATH,L"%ls\\*",directory);
    WIN32_FIND_DATAW entry; HANDLE find=FindFirstFileW(pattern,&entry);
    if (find==INVALID_HANDLE_VALUE) return GetLastError()==ERROR_FILE_NOT_FOUND;
    int empty=1;
    do { if (wcscmp(entry.cFileName,L".") && wcscmp(entry.cFileName,L"..")) { empty=0; break; } } while (FindNextFileW(find,&entry));
    FindClose(find); return empty;
}
static void cleanup(void) {
    if (*engine) DeleteFileW(engine);
    if (*scratch) RemoveDirectoryW(scratch);
    wchar_t cache[MAX_PATH]; swprintf(cache,MAX_PATH,L"%ls\\cache",root); RemoveDirectoryW(cache);
    if (*marker) DeleteFileW(marker);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,PWSTR args,int show) {
    (void)previous;(void)args;(void)show;
    if (FAILED(CoInitializeEx(NULL,COINIT_APARTMENTTHREADED))) return 1;
    BROWSEINFOW browse={0};
    browse.lpszTitle=L"ScriptoLib: создайте или выберите отдельную пустую папку. Все данные программы будут храниться в ней.";
    browse.ulFlags=BIF_RETURNONLYFSDIRS|BIF_NEWDIALOGSTYLE;
    PIDLIST_ABSOLUTE selected=SHBrowseForFolderW(&browse);
    if (!selected) { CoUninitialize(); return 0; }
    BOOL got=SHGetPathFromIDListW(selected,root); CoTaskMemFree(selected); CoUninitialize();
    DWORD attributes=GetFileAttributesW(root);
    if (!got || wcslen(root)>MAX_INSTALL_ROOT || attributes==INVALID_FILE_ATTRIBUTES || !(attributes&FILE_ATTRIBUTE_DIRECTORY) || (attributes&FILE_ATTRIBUTE_REPARSE_POINT) || !empty_directory(root)) {
        MessageBoxW(NULL,L"Нужна пустая папка с коротким путём, например D:\\ScriptoLib. Создайте её в окне выбора папки и повторите запуск установщика.",L"Установка ScriptoLib",MB_OK|MB_ICONINFORMATION); return 2;
    }
    swprintf(marker,MAX_PATH,L"%ls\\.scriptolib-setup.json",root);
    HANDLE guard=CreateFileW(marker,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_HIDDEN,NULL);
    if (guard==INVALID_HANDLE_VALUE) { MessageBoxW(NULL,L"В эту папку нельзя записывать без прав администратора. Выберите другую папку.",L"ScriptoLib",MB_OK|MB_ICONERROR); return 3; }
    const char text[]="{\"application\":\"ScriptoLib\",\"setup\":1}\n";
    DWORD count=0; WriteFile(guard,text,sizeof(text)-1,&count,NULL); CloseHandle(guard);
    wchar_t cache[MAX_PATH];swprintf(cache,MAX_PATH,L"%ls\\cache",root);
    swprintf(scratch,MAX_PATH,L"%ls\\setup",cache);
    if (!CreateDirectoryW(cache,NULL) || !CreateDirectoryW(scratch,NULL)) { cleanup(); return 3; }
    swprintf(engine,MAX_PATH,L"%ls\\engine.exe",scratch);
    HRSRC resource=FindResourceW(instance,MAKEINTRESOURCEW(101),RT_RCDATA);
    HGLOBAL loaded=resource?LoadResource(instance,resource):NULL;
    DWORD bytes=resource?SizeofResource(instance,resource):0;
    const void *content=loaded?LockResource(loaded):NULL;
    HANDLE target=CreateFileW(engine,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if (!content || !bytes || target==INVALID_HANDLE_VALUE || !WriteFile(target,content,bytes,&count,NULL) || count!=bytes) {
        if(target!=INVALID_HANDLE_VALUE)CloseHandle(target);
        cleanup();MessageBoxW(NULL,L"Не удалось подготовить установку. Проверьте свободное место в выбранной папке.",L"ScriptoLib",MB_OK|MB_ICONERROR);return 4;
    }
    CloseHandle(target);
    if (!SetEnvironmentVariableW(L"TEMP",scratch) || !SetEnvironmentVariableW(L"TMP",scratch) || !SetEnvironmentVariableW(L"SCRIPTO_SETUP_ROOT",root)) {
        cleanup();MessageBoxW(NULL,L"Не удалось настроить локальную временную папку установщика.",L"ScriptoLib",MB_OK|MB_ICONERROR);return 5;
    }
    wchar_t command[1024];swprintf(command,1024,L"\"%ls\" /D=%ls",engine,root);
    STARTUPINFOW startup={0};startup.cb=sizeof(startup);PROCESS_INFORMATION process={0};
    if(!CreateProcessW(engine,command,NULL,NULL,FALSE,0,NULL,root,&startup,&process)) {
        cleanup();MessageBoxW(NULL,L"Не удалось запустить установку.",L"ScriptoLib",MB_OK|MB_ICONERROR);return 5;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess,INFINITE);
    DWORD result=1;GetExitCodeProcess(process.hProcess,&result);CloseHandle(process.hProcess);
    cleanup();
    return (int)result;
}
