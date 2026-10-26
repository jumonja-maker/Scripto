#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>

/* Stable launcher: no registry, AppData, shortcuts, shell URL handling or temp files. */
static wchar_t root[32768], file[32768], command[32768], executable[32768];

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance; (void)previous; (void)args; (void)show;
    DWORD count = GetModuleFileNameW(NULL, root, 32768);
    if (!count || count >= 32768) return 1;
    wchar_t *slash = wcsrchr(root, L'\\');
    if (!slash) return 1;
    *slash = 0;
    if (wcslen(root) > 7000) {
        MessageBoxW(NULL,L"Путь к папке слишком длинный. Переместите папку ScriptoLib ближе к корню диска.",L"ScriptoLib",MB_OK|MB_ICONERROR);
        return 1;
    }
    swprintf(file, 32768, L"%ls\\current.txt", root);
    FILE *fp = _wfopen(file, L"rb");
    char version[64] = {0};
    if (fp) {
        size_t size = fread(version, 1, sizeof(version) - 1, fp);
        if (!feof(fp)) version[0] = 0;
        fclose(fp);
        while (size && (version[size - 1] == '\r' || version[size - 1] == '\n')) version[--size] = 0;
    }
    unsigned int major, minor, patch; int consumed = 0;
    if (sscanf(version, "%u.%u.%u%n", &major, &minor, &patch, &consumed) != 3 ||
        version[consumed]) {
        MessageBoxW(NULL, L"Не найдена версия ScriptoLib. Повторите установку в новую папку.", L"ScriptoLib", MB_OK | MB_ICONERROR);
        return 2;
    }
    version[consumed] = 0;
    wchar_t wide_version[64];
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, version, -1, wide_version, 64);
    swprintf(executable, 32768, L"%ls\\versions\\%ls\\runtime\\pythonw.exe", root, wide_version);
    int written = swprintf(command, 32768, L"\"%ls\" -B \"%ls\\versions\\%ls\\app\\bootstrap.py\" --root \"%ls\"", executable, root, wide_version, root);
    if (written < 0 || written >= 32768) return 1;
    STARTUPINFOW startup = {0}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {0};
    if (!CreateProcessW(executable, command, NULL, NULL, FALSE, 0, NULL, root, &startup, &process)) {
        MessageBoxW(NULL, L"Не удалось запустить ScriptoLib. Проверьте, что папка программы распакована полностью и доступна для записи.", L"ScriptoLib", MB_OK | MB_ICONERROR);
        return 3;
    }
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    return 0;
}
