/* Syntexophobic.exe: starts the Windows build's own Python on the program, with no console
   window. It sits in the build's folder next to python\ (and git\, MinGit), and passes its
   arguments on, so `Syntexophobic.exe mcp` is the MCP server for Claude Code: standard input
   and output reach Python when whoever started it gave some.

   python.exe gets a console of its own that is never shown (CREATE_NO_WINDOW), so the git
   commands it runs do not flash console windows either. Build (build-windows.sh does):
       windres launcher.rc -O coff -o launcher.res
       gcc -O2 -municode -mwindows launcher.c launcher.res -o Syntexophobic.exe */
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <windows.h>

static void fail(const wchar_t *what, const wchar_t *path) {
    wchar_t msg[2 * MAX_PATH];
    swprintf(msg, 2 * MAX_PATH, L"%ls\n\n%ls\n\n(error %lu)", what, path, GetLastError());
    MessageBoxW(NULL, msg, L"Syntexophobic", MB_ICONERROR);
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst; (void)prev; (void)show;
    wchar_t dir[MAX_PATH], python[MAX_PATH], found[MAX_PATH];
    if (!GetModuleFileNameW(NULL, dir, MAX_PATH)) return 1;
    *wcsrchr(dir, L'\\') = 0;
    swprintf(python, MAX_PATH, L"%ls\\python\\python.exe", dir);
    SetEnvironmentVariableW(L"SYNTEXOPHOBIC_WINDOWS_APP", dir);   /* the updater, the MCP server command */
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");     /* files and git output in UTF-8, not cp1252 */

    /* the bundled git only when there is none, so an installed Git for Windows keeps winning */
    if (!SearchPathW(NULL, L"git.exe", NULL, MAX_PATH, found, NULL)) {
        DWORD n = GetEnvironmentVariableW(L"PATH", NULL, 0);
        size_t size = n + wcslen(dir) + 16;
        wchar_t *path = malloc(size * sizeof(wchar_t)), *old = malloc((n + 1) * sizeof(wchar_t));
        old[0] = 0;
        GetEnvironmentVariableW(L"PATH", old, n + 1);
        swprintf(path, size, L"%ls\\git\\cmd;%ls", dir, old);
        SetEnvironmentVariableW(L"PATH", path);
    }

    size_t size = wcslen(python) + wcslen(args) + 32;
    wchar_t *line = malloc(size * sizeof(wchar_t));
    swprintf(line, size, L"\"%ls\" -m syntexophobic %ls", python, args);

    STARTUPINFOW si = {sizeof si};
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE), out = GetStdHandle(STD_OUTPUT_HANDLE),
           err = GetStdHandle(STD_ERROR_HANDLE);
    if (out && out != INVALID_HANDLE_VALUE) {      /* started with pipes: Claude Code, a script */
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = in;
        si.hStdOutput = out;
        si.hStdError = err;
    }
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(python, line, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
        fail(L"Could not start the program's Python:", python);
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    return (int)code;
}
