#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static wchar_t root[32768], self[32768];
static int failures = 0;

static int exists(const wchar_t *path) { return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES; }
static int ordinary_dir(const wchar_t *path) {
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY) && !(a & FILE_ATTRIBUTE_REPARSE_POINT);
}
static int safe_relative(const wchar_t *path) {
    if (!*path || *path == L'/' || *path == L'\\' || wcschr(path, L':')) return 0;
    const wchar_t *part = path;
    for (const wchar_t *p = path;; p++) {
        if (*p == L'/' || *p == L'\\' || !*p) {
            size_t n = (size_t)(p - part);
            if (!n || part[n - 1] == L'.' || part[n - 1] == L' ' || (n == 1 && part[0] == L'.') || (n == 2 && part[0] == L'.' && part[1] == L'.')) return 0;
            if (!*p) break;
            part = p + 1;
        }
    }
    return 1;
}
static int safe_ancestors(wchar_t *path, size_t base_len) {
    for (wchar_t *p = path + base_len + 1; *p; p++) {
        if (*p == L'/' || *p == L'\\') {
            wchar_t save = *p; *p = 0;
            int ok = ordinary_dir(path); *p = save;
            if (!ok) return 0;
        }
    }
    DWORD attributes = GetFileAttributesW(path);
    return attributes == INVALID_FILE_ATTRIBUTES || !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
}
static void prune(wchar_t *path, size_t stop_len) {
    wchar_t *slash;
    while ((slash = wcsrchr(path, L'\\')) != NULL && (size_t)(slash - path) > stop_len) {
        *slash = 0;
        if (!RemoveDirectoryW(path)) break;
    }
}
static void owned_version(const wchar_t *directory) {
    if (!ordinary_dir(directory)) return;
    wchar_t inventory[32768];
    swprintf(inventory, 32768, L"%ls\\.scriptolib-owned.txt", directory);
    FILE *fp = _wfopen(inventory, L"rb");
    if (!fp) { failures++; return; }
    fseek(fp, 0, SEEK_END); long size = ftell(fp); rewind(fp);
    if (size <= 0 || size > 8 * 1024 * 1024) { fclose(fp); failures++; return; }
    char *content = calloc((size_t)size + 1, 1);
    if (!content) { fclose(fp); failures++; return; }
    if (fread(content, 1, (size_t)size, fp) != (size_t)size) { free(content); fclose(fp); failures++; return; }
    fclose(fp);
    int before = failures;
    char *cursor = content;
    while (*cursor) {
        char *end = strchr(cursor, '\n');
        if (end) *end = 0;
        size_t n = strlen(cursor);
        if (n && cursor[n - 1] == '\r') cursor[n - 1] = 0;
        wchar_t relative[8192], target[32768];
        int chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, cursor, -1, relative, 8192);
        if (chars && safe_relative(relative)) {
            /* Keep retry metadata until every payload file was removed. */
            if (!_wcsicmp(relative, L".scriptolib-owned.txt") || !_wcsicmp(relative, L".scriptolib-version.json") || !_wcsicmp(relative, L"scriptolib-package.json")) {
                if (!end) break;
                cursor = end + 1;
                continue;
            }
            for (wchar_t *p = relative; *p; p++) if (*p == L'/') *p = L'\\';
            swprintf(target, 32768, L"%ls\\%ls", directory, relative);
            DWORD attributes = GetFileAttributesW(target);
            DWORD lookup_error = attributes == INVALID_FILE_ATTRIBUTES ? GetLastError() : ERROR_SUCCESS;
            if (attributes == INVALID_FILE_ATTRIBUTES && (lookup_error == ERROR_FILE_NOT_FOUND || lookup_error == ERROR_PATH_NOT_FOUND)) {
                /* A previous attempt may already have removed this file and its parents. */
            } else if (attributes != INVALID_FILE_ATTRIBUTES && safe_ancestors(target, wcslen(directory))) {
                if (!DeleteFileW(target)) failures++;
                prune(target, wcslen(directory));
            } else failures++;
        } else if (*cursor) failures++;
        if (!end) break;
        cursor = end + 1;
    }
    free(content);
    if (failures == before) {
        const wchar_t *metadata[] = {L"scriptolib-package.json", L".scriptolib-version.json"};
        wchar_t metadata_path[32768];
        for (unsigned i=0;i<sizeof(metadata)/sizeof(metadata[0]);i++) {
            swprintf(metadata_path,32768,L"%ls\\%ls",directory,metadata[i]);
            if (exists(metadata_path) && !DeleteFileW(metadata_path)) failures++;
        }
        if (failures == before && !DeleteFileW(inventory)) failures++;
        RemoveDirectoryW(directory); /* Only if empty; unknown files survive. */
    }
}
static void owned_tree(const wchar_t *directory) {
    if (!exists(directory)) return;
    if (!ordinary_dir(directory)) { failures++; return; }
    wchar_t pattern[32768], child[32768];
    swprintf(pattern, 32768, L"%ls\\*", directory);
    WIN32_FIND_DATAW entry;
    HANDLE find = FindFirstFileW(pattern, &entry);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(entry.cFileName, L".") || !wcscmp(entry.cFileName, L"..")) continue;
            swprintf(child, 32768, L"%ls\\%ls", directory, entry.cFileName);
            if (entry.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) { failures++; continue; }
            if (entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) owned_tree(child);
            else if (!DeleteFileW(child)) failures++;
        } while (FindNextFileW(find, &entry));
        FindClose(find);
    }
    if (!RemoveDirectoryW(directory)) failures++;
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance; (void)previous; (void)args; (void)show;
    if (!GetModuleFileNameW(NULL, self, 32768)) return 1;
    wcscpy(root, self);
    wchar_t *slash = wcsrchr(root, L'\\'); if (!slash) return 1; *slash = 0;
    if (wcslen(root) > 20000 || !ordinary_dir(root)) return 1;
    wchar_t path[32768];
    swprintf(path, 32768, L"%ls\\.scriptolib-install.json", root);
    FILE *marker = _wfopen(path, L"rb");
    char data[1024] = {0};
    if (marker) { fread(data, 1, sizeof(data) - 1, marker); fclose(marker); }
    if (!strstr(data, "\"application\"") || !strstr(data, "\"ScriptoLib\"")) {
        MessageBoxW(NULL, L"В этой папке нет подтверждённой установки ScriptoLib. Ничего не удалено.", L"ScriptoLib", MB_OK | MB_ICONERROR); return 2;
    }
    swprintf(path, 32768, L"%ls\\.scriptolib-maintenance.lock", root);
    HANDLE maintenance = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_HIDDEN | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (maintenance == INVALID_HANDLE_VALUE) {
        MessageBoxW(NULL,L"Другая операция обслуживания уже использует эту папку.",L"ScriptoLib",MB_OK|MB_ICONINFORMATION); return 3;
    }
    /* The OS closes/deletes this lock on all normal/error process exits. */
    swprintf(path, 32768, L"%ls\\data\\app.lock", root);
    if (exists(path)) {
        MessageBoxW(NULL, L"Сначала закройте ScriptoLib. Если программа завершилась с ошибкой, запустите и закройте её ещё раз.", L"ScriptoLib", MB_OK | MB_ICONINFORMATION); return 3;
    }
    const wchar_t *updating[] = {L"cache\\update-pending.json", L"cache\\update-helper.lock"};
    for (unsigned i=0;i<sizeof(updating)/sizeof(updating[0]);i++) {
        swprintf(path,32768,L"%ls\\%ls",root,updating[i]);
        if (exists(path)) {
            MessageBoxW(NULL,L"Дождитесь завершения обновления ScriptoLib, затем повторите удаление.",L"ScriptoLib",MB_OK|MB_ICONINFORMATION); return 3;
        }
    }
    if (MessageBoxW(NULL, L"Удалить ScriptoLib, настройки, каталог и кэш?\n\nПапка media, фильмы, музыка и другие исходные файлы останутся на месте.", L"Удаление ScriptoLib", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES) return 0;
    wchar_t versions[32768], pattern[32768];
    swprintf(versions, 32768, L"%ls\\versions", root);
    if (ordinary_dir(versions)) {
        swprintf(pattern, 32768, L"%ls\\*", versions);
        WIN32_FIND_DATAW entry; HANDLE find = FindFirstFileW(pattern, &entry);
        if (find != INVALID_HANDLE_VALUE) {
            do {
                unsigned a,b,c; int consumed = 0;
                if (swscanf(entry.cFileName, L"%u.%u.%u%n", &a,&b,&c,&consumed) != 3 || entry.cFileName[consumed]) continue;
                swprintf(path, 32768, L"%ls\\%ls", versions, entry.cFileName);
                owned_version(path);
            } while (FindNextFileW(find, &entry));
            FindClose(find);
        }
        RemoveDirectoryW(versions);
    }
    swprintf(path, 32768, L"%ls\\data", root); owned_tree(path);
    swprintf(path, 32768, L"%ls\\cache", root); owned_tree(path);
    if (failures) {
        MessageBoxW(NULL, L"Часть файлов занята или не принадлежит программе. Медиафайлы сохранены. Закройте использующие папку приложения и повторите удаление.", L"ScriptoLib", MB_OK | MB_ICONWARNING); return 4;
    }
    const wchar_t *owned[] = {L"ScriptoLib.exe", L"current.txt"};
    for (unsigned i=0;i<sizeof(owned)/sizeof(owned[0]);i++) { swprintf(path,32768,L"%ls\\%ls",root,owned[i]); if (exists(path) && !DeleteFileW(path)) failures++; }
    if (failures) {
        MessageBoxW(NULL,L"Часть файлов занята. Закройте использующие папку приложения и повторите удаление. Медиафайлы сохранены.",L"ScriptoLib",MB_OK|MB_ICONWARNING); return 4;
    }
    /* The only remaining owned file is this running executable. No temporary script. */
    SetEnvironmentVariableW(L"SCRIPTO_UNINSTALL_SELF", self);
    swprintf(path,32768,L"%ls\\.scriptolib-install.json",root);
    SetEnvironmentVariableW(L"SCRIPTO_UNINSTALL_MARKER",path);
    wchar_t shell[32768], command[32768];
    GetSystemDirectoryW(shell, 32768); wcscat(shell, L"\\cmd.exe");
    swprintf(command,32768,L"\"%ls\" /D /Q /V:OFF /C ping -n 2 127.0.0.1 >nul 2>&1 & del /F /Q \"%%SCRIPTO_UNINSTALL_SELF%%\" >nul 2>&1 & if not exist \"%%SCRIPTO_UNINSTALL_SELF%%\" del /F /Q \"%%SCRIPTO_UNINSTALL_MARKER%%\" >nul 2>&1", shell);
    STARTUPINFOW startup={0}; startup.cb=sizeof(startup); PROCESS_INFORMATION process={0};
    if (!CreateProcessW(shell, command, NULL,NULL,FALSE,CREATE_NO_WINDOW|CREATE_SUSPENDED,NULL,root,&startup,&process)) {
        MessageBoxW(NULL,L"Не удалось запустить завершающее удаление. Медиафайлы сохранены. Повторите запуск Uninstall.exe.",L"ScriptoLib",MB_OK|MB_ICONWARNING); return 4;
    }
    MessageBoxW(NULL, L"Файлы программы удалены. После закрытия этого окна удалится Uninstall.exe. Медиафайлы и посторонние файлы сохранены.", L"ScriptoLib", MB_OK | MB_ICONINFORMATION);
    ResumeThread(process.hThread);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return 0;
}
