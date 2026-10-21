#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>
#include <winuser.h>
#include <shellapi.h>
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {
constexpr wchar_t kWindowClass[] = L"VigilNativeWindowHostContainer";
constexpr DWORD kPollMs = 150;
constexpr ULONGLONG kWindowRebuildGraceMs = 3000;

struct WindowState {
  HWND hwnd = nullptr;
  HWND originalParent = nullptr;
  HWND originalOwner = nullptr;
  LONG_PTR style = 0;
  LONG_PTR exStyle = 0;
  WINDOWPLACEMENT placement{ sizeof(WINDOWPLACEMENT) };
  RECT rect{};
  bool popup = false;
  bool originalChild = false;
  bool originalVisible = false;
};

std::mutex g_outputMutex;
std::mutex g_commandsMutex;
std::vector<std::wstring> g_commands;
std::atomic<bool> g_done{ false };

std::wstring Base64ToWide(const std::wstring& encoded) {
  static const std::string alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string bytes;
  unsigned int buffer = 0;
  int bits = -8;
  for (wchar_t ch : encoded) {
    if (ch == L'=') break;
    const auto index = alphabet.find(static_cast<char>(ch));
    if (index == std::string::npos) continue;
    buffer = (buffer << 6) | static_cast<unsigned int>(index);
    bits += 6;
    if (bits >= 0) {
      bytes.push_back(static_cast<char>((buffer >> bits) & 0xff));
      bits -= 8;
    }
  }
  if (bytes.empty()) return {};
  const int needed = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(), static_cast<int>(bytes.size()), nullptr, 0);
  if (needed <= 0) return {};
  std::wstring wide(static_cast<size_t>(needed), L'\0');
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(), static_cast<int>(bytes.size()), wide.data(), needed);
  return wide;
}

std::wstring JsonEscape(const std::wstring& input) {
  std::wstring output;
  for (wchar_t ch : input) {
    switch (ch) {
      case L'"': output += L"\\\""; break;
      case L'\\': output += L"\\\\"; break;
      case L'\n': output += L"\\n"; break;
      case L'\r': output += L"\\r"; break;
      case L'\t': output += L"\\t"; break;
      default: if (ch >= 0x20) output.push_back(ch); break;
    }
  }
  return output;
}

void Emit(const std::wstring& json) {
  std::lock_guard<std::mutex> lock(g_outputMutex);
  const std::wstring line = json + L"\n";
  const int bytesNeeded = WideCharToMultiByte(CP_UTF8, 0, line.data(), static_cast<int>(line.size()), nullptr, 0, nullptr, nullptr);
  if (bytesNeeded <= 0) return;
  std::string utf8(static_cast<size_t>(bytesNeeded), '\0');
  WideCharToMultiByte(CP_UTF8, 0, line.data(), static_cast<int>(line.size()), utf8.data(), bytesNeeded, nullptr, nullptr);
  DWORD written = 0;
  WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), utf8.data(), static_cast<DWORD>(utf8.size()), &written, nullptr);
}

void EmitError(const wchar_t* stage, DWORD code, const std::wstring& message) {
  std::wstringstream line;
  line << L"{\"type\":\"error\",\"stage\":\"" << stage << L"\",\"code\":" << code
       << L",\"message\":\"" << JsonEscape(message) << L"\"}";
  Emit(line.str());
}

std::wstring StateJson(const std::wstring& state, DWORD rootPid, DWORD windowPid = 0, HWND hwnd = nullptr) {
  return L"{\"type\":\"state\",\"state\":\"" + state + L"\",\"pid\":" + std::to_wstring(rootPid) +
    L",\"windowPid\":" + std::to_wstring(windowPid) + L",\"hwnd\":" +
    std::to_wstring(reinterpret_cast<uintptr_t>(hwnd)) + L"}";
}

void SetWindowLongPtrChecked(HWND hwnd, int index, LONG_PTR value) {
  SetLastError(ERROR_SUCCESS);
  const LONG_PTR result = SetWindowLongPtrW(hwnd, index, value);
  const DWORD error = GetLastError();
  if (result == 0 && error != ERROR_SUCCESS) throw error;
}

WindowState SaveWindow(HWND hwnd, bool popup) {
  WindowState state;
  state.hwnd = hwnd;
  state.style = GetWindowLongPtrW(hwnd, GWL_STYLE);
  state.originalChild = (state.style & WS_CHILD) != 0;
  state.originalVisible = IsWindowVisible(hwnd) != FALSE;
  state.originalParent = state.originalChild ? GetParent(hwnd) : nullptr;
  state.originalOwner = state.originalChild ? nullptr : GetWindow(hwnd, GW_OWNER);
  state.exStyle = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
  state.placement.length = sizeof(WINDOWPLACEMENT);
  GetWindowPlacement(hwnd, &state.placement);
  GetWindowRect(hwnd, &state.rect);
  state.popup = popup;
  return state;
}

bool RestoreWindow(const WindowState& state) {
  if (!IsWindow(state.hwnd)) return false;
  const HWND parent = state.originalChild && IsWindow(state.originalParent) ? state.originalParent : nullptr;
  SetLastError(ERROR_SUCCESS);
  const HWND previousParent = SetParent(state.hwnd, parent);
  const DWORD parentError = GetLastError();
  if (!previousParent && parentError != ERROR_SUCCESS) return false;
  try {
    SetWindowLongPtrChecked(state.hwnd, GWL_STYLE, state.style);
    SetWindowLongPtrChecked(state.hwnd, GWL_EXSTYLE, state.exStyle);
  } catch (...) { return false; }
  if (!state.originalChild) {
    SetLastError(ERROR_SUCCESS);
    SetWindowLongPtrW(state.hwnd, GWLP_HWNDPARENT, IsWindow(state.originalOwner) ? reinterpret_cast<LONG_PTR>(state.originalOwner) : 0);
  }
  const bool parentRestored = state.originalChild
    ? GetParent(state.hwnd) == parent
    : ((GetWindowLongPtrW(state.hwnd, GWL_STYLE) & WS_CHILD) == 0 && GetWindow(state.hwnd, GW_OWNER) == (IsWindow(state.originalOwner) ? state.originalOwner : nullptr));
  if (!parentRestored) return false;
  if (!SetWindowPos(state.hwnd, nullptr, state.rect.left, state.rect.top,
    state.rect.right - state.rect.left, state.rect.bottom - state.rect.top,
    SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)) return false;
  if (!SetWindowPlacement(state.hwnd, &state.placement)) return false;
  if (state.originalVisible && !IsWindowVisible(state.hwnd)) ShowWindow(state.hwnd, SW_SHOWNOACTIVATE);
  if (state.originalVisible != (IsWindowVisible(state.hwnd) != FALSE)) return false;
  return true;
}

struct Candidate { HWND hwnd = nullptr; DWORD pid = 0; long long area = 0; std::wstring title; bool owned = false; };
struct EnumContext { const std::set<DWORD>* pids; HWND exclude; std::vector<Candidate>* windows; bool includeOwned; };

BOOL CALLBACK EnumWindowsProc(HWND hwnd, LPARAM parameter) {
  auto* context = reinterpret_cast<EnumContext*>(parameter);
  if (hwnd == context->exclude || (GetWindowLongPtrW(hwnd, GWL_STYLE) & WS_CHILD)) return TRUE;
  DWORD pid = 0;
  GetWindowThreadProcessId(hwnd, &pid);
  if (!context->pids->count(pid)) return TRUE;
  const HWND owner = GetWindow(hwnd, GW_OWNER);
  if (!context->includeOwned && owner) return TRUE;
  if (!IsWindowVisible(hwnd)) return TRUE;
  wchar_t title[512]{};
  GetWindowTextW(hwnd, title, static_cast<int>(std::size(title)));
  RECT rect{};
  if (!GetWindowRect(hwnd, &rect)) return TRUE;
  const long long area = static_cast<long long>(rect.right - rect.left) * (rect.bottom - rect.top);
  if (area <= 0) return TRUE;
  context->windows->push_back({ hwnd, pid, area, title, owner != nullptr });
  return TRUE;
}

std::vector<Candidate> FindWindows(const std::set<DWORD>& pids, HWND exclude = nullptr, bool includeOwned = false) {
  std::vector<Candidate> windows;
  EnumContext context{ &pids, exclude, &windows, includeOwned };
  EnumWindows(EnumWindowsProc, reinterpret_cast<LPARAM>(&context));
  std::sort(windows.begin(), windows.end(), [](const Candidate& a, const Candidate& b) { return a.area > b.area; });
  return windows;
}

std::wstring QuoteWindowsArg(const std::wstring& value) {
  std::wstring result = L"\"";
  unsigned int slashes = 0;
  for (wchar_t ch : value) {
    if (ch == L'\\') { ++slashes; continue; }
    if (ch == L'\"') { result.append(slashes * 2 + 1, L'\\'); result.push_back(ch); slashes = 0; continue; }
    result.append(slashes, L'\\'); slashes = 0; result.push_back(ch);
  }
  result.append(slashes * 2, L'\\');
  result.push_back(L'\"');
  return result;
}

struct Host {
  HWND parent = nullptr;
  HWND container = nullptr;
  RECT bounds{ 24, 100, 1000, 700 };
  PROCESS_INFORMATION process{};
  HANDLE job = nullptr;
  std::map<DWORD, HANDLE> trackedProcesses;
  std::set<DWORD> processIds;
  std::set<HWND> reportedPopups;
  DWORD launchPid = 0;
  DWORD mainPid = 0;
  ULONGLONG windowLostAt = 0;
  ULONGLONG discoveryStartedAt = 0;
  HWND mainWindow = nullptr;
  WindowState mainState{};
  std::vector<WindowState> popups;
  bool launched = false;
  bool attached = false;
  bool visible = false;
  bool fallback = false;
  bool detachedByUser = false;
  bool targetWindowHidden = false;
  std::wstring exePath;
  std::wstring state = L"idle";
};

bool RestoreAll(Host& host);

void EmitState(Host& host, const std::wstring& state, DWORD windowPid = 0, HWND hwnd = nullptr) {
  if (host.state == state && state != L"attached") return;
  host.state = state;
  Emit(StateJson(state, host.launchPid, windowPid, hwnd));
}

void TrackProcess(Host& host, DWORD pid, HANDLE existing = nullptr) {
  if (!pid || host.trackedProcesses.count(pid)) return;
  HANDLE process = existing ? existing : OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
  if (!process) return;
  host.trackedProcesses.emplace(pid, process);
  host.processIds.insert(pid);
}

std::set<DWORD> QueryJobPids(HANDLE job) {
  std::set<DWORD> pids;
  if (!job) return pids;
  size_t capacity = 64;
  for (int attempt = 0; attempt < 8; ++attempt) {
    const size_t bytes = sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST) + (capacity - 1) * sizeof(ULONG_PTR);
    std::vector<unsigned char> buffer(bytes);
    auto* list = reinterpret_cast<JOBOBJECT_BASIC_PROCESS_ID_LIST*>(buffer.data());
    list->NumberOfAssignedProcesses = static_cast<DWORD>(capacity);
    list->NumberOfProcessIdsInList = 0;
    if (QueryInformationJobObject(job, JobObjectBasicProcessIdList, list, static_cast<DWORD>(bytes), nullptr)) {
      const DWORD count = std::min<DWORD>(list->NumberOfProcessIdsInList, list->NumberOfAssignedProcesses);
      for (DWORD i = 0; i < count; ++i) pids.insert(static_cast<DWORD>(list->ProcessIdList[i]));
      return pids;
    }
    if (GetLastError() != ERROR_MORE_DATA) return pids;
    capacity *= 2;
  }
  return pids;
}

std::set<DWORD> RefreshProcesses(Host& host) {
  for (DWORD pid : QueryJobPids(host.job)) TrackProcess(host, pid);
  std::set<DWORD> activeParents;
  for (const auto& item : host.trackedProcesses) {
    if (WaitForSingleObject(item.second, 0) == WAIT_TIMEOUT) activeParents.insert(item.first);
  }
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot != INVALID_HANDLE_VALUE) {
    std::vector<PROCESSENTRY32W> entries;
    PROCESSENTRY32W entry{}; entry.dwSize = sizeof(entry);
    if (Process32FirstW(snapshot, &entry)) do { entries.push_back(entry); } while (Process32NextW(snapshot, &entry));
    CloseHandle(snapshot);
    bool changed = true;
    while (changed) {
      changed = false;
      for (const auto& item : entries) {
        if (activeParents.count(item.th32ParentProcessID) && !host.trackedProcesses.count(item.th32ProcessID)) {
          const size_t before = host.trackedProcesses.size();
          TrackProcess(host, item.th32ProcessID);
          if (host.trackedProcesses.size() != before) {
            activeParents.insert(item.th32ProcessID);
            changed = true;
          }
        }
      }
    }
  }
  std::set<DWORD> live;
  for (auto it = host.trackedProcesses.begin(); it != host.trackedProcesses.end();) {
    if (WaitForSingleObject(it->second, 0) == WAIT_TIMEOUT) { live.insert(it->first); ++it; }
    else { host.processIds.erase(it->first); CloseHandle(it->second); it = host.trackedProcesses.erase(it); }
  }
  return live;
}

void ClearSession(Host& host) {
  RestoreAll(host);
  for (const auto& item : host.trackedProcesses) if (item.second) CloseHandle(item.second);
  host.trackedProcesses.clear();
  host.processIds.clear();
  if (host.job) { CloseHandle(host.job); host.job = nullptr; }
  if (host.process.hThread) { CloseHandle(host.process.hThread); host.process.hThread = nullptr; }
  if (host.process.hProcess) { CloseHandle(host.process.hProcess); host.process.hProcess = nullptr; }
  host.process = {};
  host.launchPid = 0;
  host.mainPid = 0;
  host.windowLostAt = 0;
  host.launched = false;
  host.fallback = false;
  host.detachedByUser = false;
  host.targetWindowHidden = false;
  host.exePath.clear();
  host.reportedPopups.clear();
  host.discoveryStartedAt = 0;
}

void HideContainer(Host& host) {
  if (IsWindow(host.container)) ShowWindow(host.container, SW_HIDE);
  host.visible = false;
}

void UpdateBounds(Host& host, int x, int y, int width, int height) {
  host.bounds = { x, y, x + std::max(width, 1), y + std::max(height, 1) };
  if (IsWindow(host.container)) SetWindowPos(host.container, nullptr, x, y, std::max(width, 1), std::max(height, 1), SWP_NOZORDER | SWP_NOACTIVATE);
  if (host.attached && IsWindow(host.mainWindow)) {
    SetWindowPos(host.mainWindow, nullptr, 0, 0, std::max(width, 1), std::max(height, 1), SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
  }
}

bool EmbedWindow(Host& host, HWND target, bool popup) {
  if (!IsWindow(target) || !IsWindow(host.container)) return false;
  WindowState state = SaveWindow(target, popup);
  try {
    const LONG_PTR style = (state.style & ~static_cast<LONG_PTR>(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD;
    SetWindowLongPtrChecked(target, GWL_STYLE, style);
    if (state.exStyle & WS_EX_APPWINDOW) SetWindowLongPtrChecked(target, GWL_EXSTYLE, state.exStyle & ~static_cast<LONG_PTR>(WS_EX_APPWINDOW));
    SetLastError(ERROR_SUCCESS);
    const HWND previousParent = SetParent(target, host.container);
    const DWORD error = GetLastError();
    const HWND actualParent = GetParent(target);
    Emit(L"{\"type\":\"set-parent-result\",\"applied\":" + std::wstring(actualParent == host.container ? L"true" : L"false") +
      L",\"returnNull\":" + (previousParent ? L"false" : L"true") + L",\"lastError\":" + std::to_wstring(error) +
      L",\"parentHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(actualParent)) + L",\"expectedParentHwnd\":" +
      std::to_wstring(reinterpret_cast<uintptr_t>(host.container)) + L"}");
    if (actualParent != host.container) {
      RestoreWindow(state);
      EmitError(L"set-parent", error ? error : ERROR_ACCESS_DENIED, L"Windows rejected SetParent; target remains an independent window.");
      return false;
    }
    const int width = host.bounds.right - host.bounds.left;
    const int height = host.bounds.bottom - host.bounds.top;
    int x = 0, y = 0, w = width, h = height;
    if (popup) {
      w = std::max(320, std::min(760, width - 32));
      h = std::max(180, std::min(520, height - 32));
      x = (width - w) / 2; y = (height - h) / 2;
    }
    if (!SetWindowPos(target, popup ? HWND_TOP : nullptr, x, y, w, h, SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)) {
      const DWORD positionError = GetLastError();
      RestoreWindow(state);
      EmitError(L"set-position", positionError, L"Windows rejected the hosted window resize; the original window was restored.");
      return false;
    }
    if (popup) return false;
    host.mainWindow = target; host.mainState = state; host.mainPid = 0;
    GetWindowThreadProcessId(target, &host.mainPid);
    host.attached = true;
    return true;
  } catch (DWORD error) {
    RestoreWindow(state);
    EmitError(L"set-style", error, L"Could not change the external window style; it remains standalone.");
    return false;
  }
}

bool RestoreAll(Host& host) {
  bool ok = !host.attached || RestoreWindow(host.mainState);
  host.attached = false;
  host.mainWindow = nullptr;
  host.mainPid = 0;
  HideContainer(host);
  return ok;
}

void Fallback(Host& host, const std::wstring& reason) {
  RestoreAll(host);
  host.fallback = true;
  EmitState(host, L"fallback");
  Emit(L"{\"type\":\"fallback\",\"reason\":\"" + JsonEscape(reason) + L"\"}");
}

void ProcessCommand(Host& host, const std::wstring& line) {
  std::vector<std::wstring> fields;
  std::wistringstream stream(line);
  std::wstring part;
  while (std::getline(stream, part, L'\t')) fields.push_back(part);
  if (fields.empty()) return;
  const auto number = [&](size_t index, int fallback) { try { return index < fields.size() ? std::stoi(fields[index]) : fallback; } catch (...) { return fallback; } };
  const std::wstring& command = fields[0];
  if (command == L"START" && fields.size() >= 7) {
    const std::wstring exe = Base64ToWide(fields[1]);
    const std::wstring args = Base64ToWide(fields[2]);
    if (exe.empty()) { EmitError(L"start", ERROR_INVALID_PARAMETER, L"The EXE path is empty or invalid UTF-8."); return; }
    if (host.launched && RefreshProcesses(host).empty()) {
      Emit(L"{\"type\":\"exited\",\"reason\":\"all-target-processes-exited\"}");
      ClearSession(host);
    }
    if (host.launched && _wcsicmp(host.exePath.c_str(), exe.c_str()) != 0) {
      const auto activePids = RefreshProcesses(host);
      const auto remainingWindows = FindWindows(activePids, host.container);
      if (host.detachedByUser || (host.fallback && remainingWindows.empty())) {
        const DWORD releasedPid = host.launchPid;
        ClearSession(host);
        Emit(L"{\"type\":\"session-released\",\"pid\":" + std::to_wstring(releasedPid) + L",\"processEnded\":false,\"reason\":\"switch-to-another-profile\"}");
      } else {
        EmitError(L"start", ERROR_BUSY, L"Another configured application still has an active window. Close it or detach it before switching profiles.");
        EmitState(host, L"busy");
        return;
      }
    }
    if (!host.launched) {
      std::wstring commandLine = QuoteWindowsArg(exe);
      if (!args.empty()) commandLine += L" " + args;
      STARTUPINFOW startup{}; startup.cb = sizeof(startup);
      PROCESS_INFORMATION process{};
      const size_t slash = exe.find_last_of(L"\\/");
      const std::wstring workingDirectory = slash == std::wstring::npos ? L"" : exe.substr(0, slash);
      if (!CreateProcessW(exe.c_str(), commandLine.data(), nullptr, nullptr, FALSE, CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED, nullptr,
          workingDirectory.empty() ? nullptr : workingDirectory.c_str(), &startup, &process)) {
        EmitError(L"start", GetLastError(), L"CreateProcessW could not start the selected EXE."); return;
      }
      host.process = process;
      host.launchPid = process.dwProcessId;
      host.exePath = exe;
      host.job = CreateJobObjectW(nullptr, nullptr);
      bool jobAssigned = host.job && AssignProcessToJobObject(host.job, process.hProcess);
      if (!jobAssigned && host.job) { CloseHandle(host.job); host.job = nullptr; }
      TrackProcess(host, process.dwProcessId, process.hProcess);
      host.process.hProcess = nullptr;
      const DWORD resume = ResumeThread(process.hThread);
      if (resume == static_cast<DWORD>(-1)) {
        EmitError(L"resume", GetLastError(), L"The selected application was created but its initial thread could not be resumed.");
      }
      host.launched = true; host.detachedByUser = false; host.fallback = false;
      host.discoveryStartedAt = GetTickCount64();
      Emit(L"{\"type\":\"started\",\"pid\":" + std::to_wstring(host.launchPid) + L",\"jobTracking\":" + (jobAssigned ? L"true" : L"false") + L"}");
      if (!jobAssigned) Emit(L"{\"type\":\"tracking-fallback\",\"mode\":\"observed-process-tree\",\"message\":\"Windows did not allow Job Object assignment; the helper will retain observed descendant process handles.\"}");
    }
    UpdateBounds(host, number(3, 0), number(4, 0), number(5, 1), number(6, 1));
    host.fallback = false; host.detachedByUser = false;
    EmitState(host, L"starting");
    return;
  }
  if (command == L"BOUNDS" && fields.size() >= 5) { UpdateBounds(host, number(1,0), number(2,0), number(3,1), number(4,1)); return; }
  if (command == L"SHOW") { if (IsWindow(host.container)) ShowWindow(host.container, SW_SHOWNA); host.visible = true; return; }
  if (command == L"HIDE") { HideContainer(host); return; }
  if (command == L"FOCUS") {
    if (host.attached && IsWindow(host.mainWindow)) {
      DWORD targetPid = 0;
      const DWORD targetThread = GetWindowThreadProcessId(host.mainWindow, &targetPid);
      DWORD parentPid = 0;
      const DWORD parentThread = GetWindowThreadProcessId(host.parent, &parentPid);
      const DWORD currentThread = GetCurrentThreadId();
      const bool targetInputAttached = AttachThreadInput(currentThread, targetThread, TRUE) != FALSE;
      const bool parentInputAttached = parentThread == targetThread || AttachThreadInput(currentThread, parentThread, TRUE) != FALSE;
      const bool foregroundRequested = SetForegroundWindow(host.parent) != FALSE;
      SetFocus(host.mainWindow);
      const bool focusSet = GetFocus() == host.mainWindow;
      if (parentThread != targetThread && parentInputAttached) AttachThreadInput(currentThread, parentThread, FALSE);
      if (targetInputAttached) AttachThreadInput(currentThread, targetThread, FALSE);
      Emit(L"{\"type\":\"focus\",\"pid\":" + std::to_wstring(targetPid) + L",\"hwnd\":" +
        std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L",\"foregroundRequested\":" + (foregroundRequested ? L"true" : L"false") +
        L",\"targetInputAttached\":" + (targetInputAttached ? L"true" : L"false") + L",\"parentInputAttached\":" +
        (parentInputAttached ? L"true" : L"false") + L",\"focusSet\":" + (focusSet ? L"true" : L"false") + L"}");
    }
    return;
  }
  if (command == L"ATTACH") { host.fallback = false; host.detachedByUser = false; host.discoveryStartedAt = GetTickCount64(); return; }
  if (command == L"DETACH") {
    const HWND detachedHwnd = host.mainWindow;
    const LONG_PTR originalStyle = host.mainState.style;
    const bool restored = RestoreAll(host);
    host.detachedByUser = true;
    const bool valid = IsWindow(detachedHwnd) != FALSE;
    const bool visible = valid && IsWindowVisible(detachedHwnd);
    const LONG_PTR style = valid ? GetWindowLongPtrW(detachedHwnd, GWL_STYLE) : 0;
    Emit(L"{\"type\":\"detached\",\"restored\":" + std::wstring(restored ? L"true" : L"false") + L",\"hwnd\":" +
      std::to_wstring(reinterpret_cast<uintptr_t>(detachedHwnd)) + L",\"valid\":" + (valid ? L"true" : L"false") +
      L",\"visible\":" + (visible ? L"true" : L"false") + L",\"parentHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(valid ? GetParent(detachedHwnd) : nullptr)) +
      L",\"style\":" + std::to_wstring(style) + L",\"originalStyle\":" + std::to_wstring(originalStyle) + L"}");
    EmitState(host, L"detached");
    return;
  }
  if (command == L"CLOSE_APP") {
    if (host.attached && IsWindow(host.mainWindow)) PostMessageW(host.mainWindow, WM_CLOSE, 0, 0);
    else if (host.launchPid) {
      const auto pids = RefreshProcesses(host);
      const auto windows = FindWindows(pids);
      if (!windows.empty()) PostMessageW(windows.front().hwnd, WM_CLOSE, 0, 0);
    }
    Emit(L"{\"type\":\"close-requested\",\"forced\":false}");
    return;
  }
  if (command == L"STATUS") { Emit(StateJson(host.state, host.launchPid, host.mainPid, host.mainWindow)); return; }
  if (command == L"QUIT") { ClearSession(host); g_done = true; return; }
}

void ReaderThread() {
  std::string utf8;
  while (!g_done && std::getline(std::cin, utf8)) {
    const int needed = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
    if (needed <= 0) continue;
    std::wstring line(static_cast<size_t>(needed), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8.data(), static_cast<int>(utf8.size()), line.data(), needed);
    std::lock_guard<std::mutex> lock(g_commandsMutex);
    g_commands.push_back(line);
  }
  g_done = true;
}
}  // namespace

int wmain(int argc, wchar_t** argv) {
  SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
  if (argc < 2) return 2;
  HWND parent = reinterpret_cast<HWND>(static_cast<uintptr_t>(_wcstoui64(argv[1], nullptr, 16)));
  if (!IsWindow(parent)) return 3;

  WNDCLASSW windowClass{};
  windowClass.lpfnWndProc = DefWindowProcW;
  windowClass.hInstance = GetModuleHandleW(nullptr);
  windowClass.lpszClassName = kWindowClass;
  RegisterClassW(&windowClass);
  HWND container = CreateWindowExW(0, kWindowClass, L"", WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
    0, 0, 1, 1, parent, nullptr, windowClass.hInstance, nullptr);
  if (!container) { EmitError(L"container", GetLastError(), L"Could not create a native child container in the Electron window."); return 4; }

  Host host;
  host.parent = parent;
  host.container = container;
  HideContainer(host);
  Emit(L"{\"type\":\"ready\",\"containerCreated\":true,\"containerHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(container)) + L",\"dpiAwareness\":\"per-monitor-v2\"}");
  std::thread reader(ReaderThread);
  while (!g_done) {
    std::vector<std::wstring> commands;
    {
      std::lock_guard<std::mutex> lock(g_commandsMutex);
      commands.swap(g_commands);
    }
    for (const auto& command : commands) ProcessCommand(host, command);

    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }

    if (!IsWindow(parent)) { ClearSession(host); g_done = true; break; }
    if (host.launched) {
      const auto pids = RefreshProcesses(host);
      if (pids.empty()) {
        const DWORD oldPid = host.launchPid;
        ClearSession(host);
        Emit(L"{\"type\":\"exited\",\"pid\":" + std::to_wstring(oldPid) + L",\"reason\":\"all-target-processes-exited\"}");
        EmitState(host, L"idle");
      } else {
        if (host.attached && !IsWindow(host.mainWindow)) {
          host.mainWindow = nullptr; host.mainPid = 0; host.attached = false;
          host.discoveryStartedAt = GetTickCount64();
      host.windowLostAt = GetTickCount64();
      Emit(L"{\"type\":\"window-lost\",\"reason\":\"hwnd-destroyed\"}");
      EmitState(host, L"window-rebuilding");
        }
        if (host.attached && IsWindow(host.mainWindow)) {
          const bool targetVisible = IsWindowVisible(host.mainWindow) != FALSE;
          if (host.visible && !targetVisible && !host.targetWindowHidden) {
            host.targetWindowHidden = true;
            Emit(L"{\"type\":\"window-hidden\",\"pid\":" + std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L"}");
            EmitState(host, L"window-hidden", host.mainPid, host.mainWindow);
          } else if (targetVisible && host.targetWindowHidden) {
            host.targetWindowHidden = false;
            Emit(L"{\"type\":\"window-visible\",\"pid\":" + std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L"}");
            EmitState(host, L"attached", host.mainPid, host.mainWindow);
          }
          const auto secondary = FindWindows(pids, host.container, true);
          for (const auto& candidate : secondary) {
            if (candidate.hwnd == host.mainWindow || !candidate.owned || host.reportedPopups.count(candidate.hwnd)) continue;
            host.reportedPopups.insert(candidate.hwnd);
            Emit(L"{\"type\":\"popup-standalone\",\"pid\":" + std::to_wstring(candidate.pid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(candidate.hwnd)) + L",\"owner\":" + std::to_wstring(reinterpret_cast<uintptr_t>(GetWindow(candidate.hwnd, GW_OWNER))) + L"}");
          }
          for (auto it = host.reportedPopups.begin(); it != host.reportedPopups.end();) {
            if (!IsWindow(*it)) it = host.reportedPopups.erase(it); else ++it;
          }
        } else if (!host.fallback && !host.detachedByUser) {
          const auto windows = FindWindows(pids, host.container);
          if (!windows.empty()) {
            if (!EmbedWindow(host, windows.front().hwnd, false)) Fallback(host, L"The target window could not be embedded; it is available as a standalone window.");
            else {
              if (host.windowLostAt) Emit(L"{\"type\":\"recreated\",\"pid\":" + std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L"}");
              host.windowLostAt = 0;
              ShowWindow(host.container, host.visible ? SW_SHOWNA : SW_HIDE);
              RECT attachedRect{}; GetWindowRect(windows.front().hwnd, &attachedRect);
              Emit(L"{\"type\":\"attached\",\"pid\":" + std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(windows.front().hwnd)) + L",\"parentHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(GetParent(windows.front().hwnd))) + L",\"dpi\":" + std::to_wstring(GetDpiForWindow(windows.front().hwnd)) + L",\"screenRect\":[" + std::to_wstring(attachedRect.left) + L"," + std::to_wstring(attachedRect.top) + L"," + std::to_wstring(attachedRect.right) + L"," + std::to_wstring(attachedRect.bottom) + L"]}");
              EmitState(host, L"attached", host.mainPid, host.mainWindow);
            }
          } else if (host.windowLostAt && GetTickCount64() - host.windowLostAt > kWindowRebuildGraceMs) {
            const DWORD oldPid = host.launchPid;
            const size_t liveCount = pids.size();
            Emit(L"{\"type\":\"window-closed-resident\",\"pid\":" + std::to_wstring(oldPid) + L",\"liveProcessCount\":" + std::to_wstring(liveCount) + L",\"processEnded\":false}");
            EmitState(host, L"resident", 0, nullptr);
            ClearSession(host);
            EmitState(host, L"idle");
          } else if (!host.windowLostAt && GetTickCount64() - host.discoveryStartedAt > 30000) {
            Fallback(host, L"No visible top-level window was found in the launched process family within 30 seconds; the process remains running independently.");
          }
        }
      }
    }
    Sleep(kPollMs);
  }
  ClearSession(host);
  if (IsWindow(container)) DestroyWindow(container);
  if (reader.joinable()) { if (g_done) reader.detach(); else reader.join(); }
  return 0;
}
