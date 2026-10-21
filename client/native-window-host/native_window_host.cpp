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
#include <cstdlib>
#include <cwctype>
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

struct RestoreResult {
  bool restored = false;
  bool windowGone = false;
  const wchar_t* stage = L"unknown";
  DWORD error = ERROR_SUCCESS;
};

enum class RestoreIntent { none, detach, fallback, clearSession, quit };

std::mutex g_outputMutex;
std::mutex g_commandsMutex;
std::vector<std::wstring> g_commands;
std::atomic<bool> g_done{ false };
std::atomic<bool> g_inputClosed{ false };

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

RestoreResult RestoreWindow(const WindowState& state, const std::wstring& injectedFailure = {}) {
  if (!IsWindow(state.hwnd)) return { false, true, L"window-gone", ERROR_INVALID_WINDOW_HANDLE };
  const HWND parent = state.originalChild && IsWindow(state.originalParent) ? state.originalParent : nullptr;
  if (!state.originalChild && injectedFailure == L"set-parent") return { false, false, L"set-parent", ERROR_ACCESS_DENIED };
  if (state.originalChild && !IsWindow(state.originalParent)) return { false, false, L"original-parent", ERROR_INVALID_WINDOW_HANDLE };
  DWORD error = ERROR_SUCCESS;
  if (GetWindowLongPtrW(state.hwnd, GWL_STYLE) != state.style) {
    SetLastError(ERROR_SUCCESS);
    SetWindowLongPtrW(state.hwnd, GWL_STYLE, state.style);
    error = GetLastError();
    if (error != ERROR_SUCCESS) return { false, false, L"style", error };
  }
  if (GetWindowLongPtrW(state.hwnd, GWL_EXSTYLE) != state.exStyle) {
    SetLastError(ERROR_SUCCESS);
    SetWindowLongPtrW(state.hwnd, GWL_EXSTYLE, state.exStyle);
    error = GetLastError();
    if (error != ERROR_SUCCESS) return { false, false, L"extended-style", error };
  }
  if (!state.originalChild) {
    const HWND expectedOwner = IsWindow(state.originalOwner) ? state.originalOwner : nullptr;
    if (GetWindow(state.hwnd, GW_OWNER) != expectedOwner) {
      SetLastError(ERROR_SUCCESS);
      SetWindowLongPtrW(state.hwnd, GWLP_HWNDPARENT, reinterpret_cast<LONG_PTR>(expectedOwner));
      error = GetLastError();
      if (error != ERROR_SUCCESS) return { false, false, L"owner", error };
    }
  }
  if (injectedFailure == L"set-parent") return { false, false, L"set-parent", ERROR_ACCESS_DENIED };
  SetLastError(ERROR_SUCCESS);
  SetParent(state.hwnd, parent);
  error = GetLastError();
  if (error != ERROR_SUCCESS && GetParent(state.hwnd) != parent) return { false, false, L"set-parent", error };
  const bool parentRestored = state.originalChild
    ? GetParent(state.hwnd) == parent
    : ((GetWindowLongPtrW(state.hwnd, GWL_STYLE) & WS_CHILD) == 0 && GetWindow(state.hwnd, GW_OWNER) == (IsWindow(state.originalOwner) ? state.originalOwner : nullptr));
  if (!parentRestored) return { false, false, L"verify-parent", ERROR_INVALID_STATE };
  if (injectedFailure == L"set-position") return { false, false, L"set-position", ERROR_ACCESS_DENIED };
  if (!SetWindowPos(state.hwnd, nullptr, state.rect.left, state.rect.top,
    state.rect.right - state.rect.left, state.rect.bottom - state.rect.top,
    SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)) return { false, false, L"set-position", GetLastError() };
  if (!SetWindowPlacement(state.hwnd, &state.placement)) return { false, false, L"set-placement", GetLastError() };
  if (state.originalVisible && !IsWindowVisible(state.hwnd)) ShowWindow(state.hwnd, SW_SHOWNOACTIVATE);
  if (GetWindowLongPtrW(state.hwnd, GWL_STYLE) != state.style) return { false, false, L"verify-style", ERROR_INVALID_STATE };
  if (GetWindowLongPtrW(state.hwnd, GWL_EXSTYLE) != state.exStyle) return { false, false, L"verify-extended-style", ERROR_INVALID_STATE };
  RECT restoredRect{};
  if (!GetWindowRect(state.hwnd, &restoredRect)) return { false, false, L"verify-position", GetLastError() };
  if (restoredRect.left != state.rect.left || restoredRect.top != state.rect.top ||
      restoredRect.right != state.rect.right || restoredRect.bottom != state.rect.bottom) {
    return { false, false, L"verify-position", ERROR_INVALID_STATE };
  }
  WINDOWPLACEMENT restoredPlacement{};
  restoredPlacement.length = sizeof(restoredPlacement);
  if (!GetWindowPlacement(state.hwnd, &restoredPlacement)) return { false, false, L"verify-placement", GetLastError() };
  if (restoredPlacement.showCmd != state.placement.showCmd) return { false, false, L"verify-placement", ERROR_INVALID_STATE };
  if (state.originalVisible != (IsWindowVisible(state.hwnd) != FALSE)) return { false, false, L"verify-visibility", ERROR_INVALID_STATE };
  return { true, false, L"complete", ERROR_SUCCESS };
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
  RestoreIntent restoreIntent = RestoreIntent::none;
  ULONGLONG nextRestoreAttempt = 0;
  std::wstring failNextRestoreStage;
  HWND lastRestoredWindow = nullptr;
  DWORD lastRestoredPid = 0;
  LONG_PTR lastRestoredStyle = 0;
  std::wstring restoreFailureReason;
  bool lastRestoreWindowGone = false;
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

std::wstring Lowercase(std::wstring value) {
  std::transform(value.begin(), value.end(), value.begin(), [](wchar_t c) { return static_cast<wchar_t>(::towlower(c)); });
  return value;
}

// 目标路径可能来自用户输入或 Electron，分隔符正反斜杠混用、还可能重复；
// 比对前统一成小写单反斜杠，否则同一条路径会因写法不同而匹配不上。
std::wstring NormalizePathForCompare(const std::wstring& value) {
  std::wstring result;
  result.reserve(value.size());
  wchar_t previous = 0;
  for (wchar_t ch : value) {
    if (ch == L'/') ch = L'\\';
    if (ch == L'\\' && previous == L'\\') continue;
    result.push_back(static_cast<wchar_t>(::towlower(ch)));
    previous = ch;
  }
  return result;
}

// Launcher-style applications (Jianying, some portable builds) hand off to another
// executable in the same folder and exit immediately. The process family then looks
// empty even though the application is running, so adopt same-folder processes by
// image path instead of relying on parent links that the launcher already broke.
bool AdoptProcessesInDirectory(Host& host, const std::wstring& exePath) {
  if (exePath.empty()) return false;
  const size_t slash = exePath.find_last_of(L"\\/");
  if (slash == std::wstring::npos) return false;
  const std::wstring prefix = NormalizePathForCompare(exePath.substr(0, slash + 1));
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE) return false;
  std::vector<DWORD> matches;
  DWORD scanned = 0, resolved = 0;
  std::wstring sample;
  PROCESSENTRY32W entry{}; entry.dwSize = sizeof(entry);
  if (Process32FirstW(snapshot, &entry)) {
    do {
      ++scanned;
      if (entry.th32ProcessID == GetCurrentProcessId() || host.trackedProcesses.count(entry.th32ProcessID)) continue;
      HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ProcessID);
      if (!process) continue;
      wchar_t buffer[MAX_PATH]{};
      DWORD size = static_cast<DWORD>(MAX_PATH);
      const bool ok = QueryFullProcessImageNameW(process, 0, buffer, &size) != FALSE;
      CloseHandle(process);
      if (!ok || size == 0) continue;
      ++resolved;
      if (sample.empty()) sample.assign(buffer, size);
      const std::wstring path = NormalizePathForCompare(std::wstring(buffer, size));
      if (path.size() <= prefix.size() || path.compare(0, prefix.size(), prefix) != 0) continue;
      matches.push_back(entry.th32ProcessID);
      if (matches.size() > 64) break;
    } while (Process32NextW(snapshot, &entry));
  }
  CloseHandle(snapshot);
  if (matches.empty()) {
    if (GetEnvironmentVariableW(L"VIGIL_P15_ADOPT_DEBUG", nullptr, 0) > 0) {
      Emit(L"{\"type\":\"adopt-miss\",\"prefix\":\"" + JsonEscape(prefix) + L"\",\"scanned\":" + std::to_wstring(scanned) +
        L",\"resolved\":" + std::to_wstring(resolved) + L",\"sample\":\"" + JsonEscape(sample) + L"\"}");
    }
    return false;
  }
  for (DWORD pid : matches) TrackProcess(host, pid);
  return true;
}


void ClearSessionState(Host& host) {
  for (const auto& item : host.trackedProcesses) if (item.second) CloseHandle(item.second);
  host.trackedProcesses.clear();
  host.processIds.clear();
  if (host.job) { CloseHandle(host.job); host.job = nullptr; }
  if (host.process.hThread) { CloseHandle(host.process.hThread); host.process.hThread = nullptr; }
  if (host.process.hProcess) { CloseHandle(host.process.hProcess); host.process.hProcess = nullptr; }
  host.process = {};
  host.launchPid = 0;
  host.mainPid = 0;
  host.mainWindow = nullptr;
  host.mainState = {};
  host.attached = false;
  host.windowLostAt = 0;
  host.launched = false;
  host.fallback = false;
  host.detachedByUser = false;
  host.targetWindowHidden = false;
  host.exePath.clear();
  host.reportedPopups.clear();
  host.discoveryStartedAt = 0;
  host.restoreIntent = RestoreIntent::none;
  host.nextRestoreAttempt = 0;
}

void EmitRestoreResult(const wchar_t* type, const RestoreResult& result, HWND hwnd) {
  Emit(L"{\"type\":\"" + std::wstring(type) + L"\",\"stage\":\"" + result.stage +
    L"\",\"code\":" + std::to_wstring(result.error) + L",\"hwnd\":" +
    std::to_wstring(reinterpret_cast<uintptr_t>(hwnd)) + L",\"valid\":" +
    (IsWindow(hwnd) ? L"true" : L"false") + L",\"parentHwnd\":" +
    std::to_wstring(reinterpret_cast<uintptr_t>(IsWindow(hwnd) ? GetParent(hwnd) : nullptr)) +
    L",\"ownerHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(IsWindow(hwnd) ? GetWindow(hwnd, GW_OWNER) : nullptr)) +
    L",\"style\":" + std::to_wstring(IsWindow(hwnd) ? GetWindowLongPtrW(hwnd, GWL_STYLE) : 0) +
    L",\"exStyle\":" + std::to_wstring(IsWindow(hwnd) ? GetWindowLongPtrW(hwnd, GWL_EXSTYLE) : 0) +
    L",\"visible\":" + (IsWindow(hwnd) && IsWindowVisible(hwnd) ? L"true" : L"false") + L"}");
}

void HideContainer(Host& host) {
  if (IsWindow(host.container)) ShowWindow(host.container, SW_HIDE);
  if (IsWindow(host.mainWindow)) ShowWindow(host.mainWindow, SW_HIDE);
  host.visible = false;
}

void UpdateBounds(Host& host, int x, int y, int width, int height) {
  // bounds 是目标窗口的屏幕物理像素矩形；跟随模式下直接贴上去，不再经过容器。
  host.bounds = { x, y, x + std::max(width, 1), y + std::max(height, 1) };
  if (host.attached && IsWindow(host.mainWindow) && host.visible) {
    SetWindowPos(host.mainWindow, nullptr, x, y, std::max(width, 1), std::max(height, 1),
      SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
  }
}

void ApplyVisibility(Host& host) {
  if (!host.attached || !IsWindow(host.mainWindow)) return;
  if (host.visible) {
    const int width = std::max<int>(1, static_cast<int>(host.bounds.right - host.bounds.left));
    const int height = std::max<int>(1, static_cast<int>(host.bounds.bottom - host.bounds.top));
    SetWindowPos(host.mainWindow, HWND_TOP, host.bounds.left, host.bounds.top, width, height,
      SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER);
  } else {
    ShowWindow(host.mainWindow, SW_HIDE);
  }
}

bool EmbedWindow(Host& host, HWND target, bool popup) {
  // 弹窗保持独立显示，不进工作台区域。
  if (!IsWindow(target) || popup) return false;
  WindowState state = SaveWindow(target, false);
  host.mainWindow = target;
  host.mainState = state;
  host.mainPid = 0;
  GetWindowThreadProcessId(target, &host.mainPid);
  host.attached = true;
  try {
    // 跟随模式：跨进程 SetParent 的窗口在 Win10/11 拿不到 DWM 合成，画面上什么都没有。
    // 所以不改父窗口，只去掉边框、挂到 Vigil 名下当 owned window，再把它贴在绑定区域上。
    const LONG_PTR style = (state.style & ~static_cast<LONG_PTR>(WS_POPUP | WS_CAPTION | WS_THICKFRAME |
      WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)) | WS_POPUP;
    SetWindowLongPtrChecked(target, GWL_STYLE, style);
    const LONG_PTR exStyle = state.exStyle & ~static_cast<LONG_PTR>(WS_EX_APPWINDOW);
    if (exStyle != state.exStyle) SetWindowLongPtrChecked(target, GWL_EXSTYLE, exStyle);
    SetLastError(ERROR_SUCCESS);
    SetWindowLongPtrW(target, GWLP_HWNDPARENT, reinterpret_cast<LONG_PTR>(host.parent));
    const DWORD ownerError = GetLastError();
    const HWND owner = GetWindow(target, GW_OWNER);
    Emit(L"{\"type\":\"follow-result\",\"style\":" + std::to_wstring(style) +
      L",\"ownerHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(owner)) +
      L",\"expectedOwnerHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.parent)) +
      L",\"lastError\":" + std::to_wstring(ownerError) + L"}");
    const int width = std::max<int>(1, static_cast<int>(host.bounds.right - host.bounds.left));
    const int height = std::max<int>(1, static_cast<int>(host.bounds.bottom - host.bounds.top));
    // 软件常记住上次的最大化状态，先还原，否则它会自己铺满整块屏幕。
    if (state.placement.showCmd == SW_SHOWMAXIMIZED || state.placement.showCmd == SW_SHOWMINIMIZED ||
        IsZoomed(target) || IsIconic(target)) {
      ShowWindow(target, SW_RESTORE);
    }
    if (!SetWindowPos(target, HWND_TOP, host.bounds.left, host.bounds.top, width, height,
        SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOOWNERZORDER)) {
      EmitError(L"set-position", GetLastError(), L"Windows rejected the window placement; the original window was restored.");
      return false;
    }
    RECT applied{};
    GetWindowRect(target, &applied);
    Emit(L"{\"type\":\"follow-applied\",\"rect\":[" + std::to_wstring(applied.left) + L"," + std::to_wstring(applied.top) +
      L"," + std::to_wstring(applied.right) + L"," + std::to_wstring(applied.bottom) + L"],\"requested\":[" +
      std::to_wstring(host.bounds.left) + L"," + std::to_wstring(host.bounds.top) + L"," +
      std::to_wstring(width) + L"," + std::to_wstring(height) + L"]}");
    return true;
  } catch (DWORD error) {
    EmitError(L"set-style", error, L"Could not change the external window style; it remains standalone.");
    return false;
  }
}

bool RestoreAll(Host& host) {
  if (!host.mainWindow) {
    host.attached = false;
    HideContainer(host);
    return true;
  }
  const HWND hwnd = host.mainWindow;
  if (!IsWindow(hwnd)) {
    const RestoreResult gone{ false, true, L"window-gone", ERROR_INVALID_WINDOW_HANDLE };
    EmitRestoreResult(L"restore-window-gone", gone, hwnd);
    host.lastRestoreWindowGone = true;
    host.mainWindow = nullptr;
    host.mainPid = 0;
    host.mainState = {};
    host.attached = false;
    HideContainer(host);
    return true;
  }
  const std::wstring injectedFailure = host.failNextRestoreStage;
  host.failNextRestoreStage.clear();
  const RestoreResult result = RestoreWindow(host.mainState, injectedFailure);
  EmitRestoreResult(result.restored ? L"restore-complete" : L"restore-failed", result, hwnd);
  if (result.windowGone) {
    host.lastRestoreWindowGone = true;
    host.mainWindow = nullptr;
    host.mainPid = 0;
    host.mainState = {};
    host.attached = false;
    HideContainer(host);
    return true;
  }
  if (!result.restored) return false;
  host.lastRestoreWindowGone = false;
  host.mainWindow = nullptr;
  host.mainPid = 0;
  host.mainState = {};
  host.attached = false;
  HideContainer(host);
  return true;
}

void CompleteRestoreIntent(Host& host, RestoreIntent intent) {
  host.restoreIntent = RestoreIntent::none;
  host.nextRestoreAttempt = 0;
  switch (intent) {
    case RestoreIntent::detach:
      host.detachedByUser = true;
      host.fallback = false;
      Emit(L"{\"type\":\"detached\",\"restored\":" + std::wstring(host.lastRestoreWindowGone ? L"false" : L"true") +
        L",\"windowGone\":" + (host.lastRestoreWindowGone ? L"true" : L"false") + L",\"hwnd\":" +
        std::to_wstring(reinterpret_cast<uintptr_t>(host.lastRestoredWindow)) + L",\"pid\":" + std::to_wstring(host.lastRestoredPid) +
        L",\"valid\":" + (IsWindow(host.lastRestoredWindow) ? L"true" : L"false") + L",\"visible\":" +
        (IsWindow(host.lastRestoredWindow) && IsWindowVisible(host.lastRestoredWindow) ? L"true" : L"false") + L",\"parentHwnd\":" +
        std::to_wstring(reinterpret_cast<uintptr_t>(IsWindow(host.lastRestoredWindow) ? GetParent(host.lastRestoredWindow) : nullptr)) + L",\"style\":" +
        std::to_wstring(IsWindow(host.lastRestoredWindow) ? GetWindowLongPtrW(host.lastRestoredWindow, GWL_STYLE) : 0) +
        L",\"originalStyle\":" + std::to_wstring(host.lastRestoredStyle) + L"}");
      EmitState(host, L"detached");
      break;
    case RestoreIntent::fallback:
      host.fallback = true;
      host.detachedByUser = false;
      Emit(L"{\"type\":\"fallback\",\"reason\":\"" + JsonEscape(host.restoreFailureReason.empty() ? L"The target window was restored as a standalone window." : host.restoreFailureReason) + L"\"}");
      host.restoreFailureReason.clear();
      EmitState(host, L"fallback");
      break;
    case RestoreIntent::clearSession:
      ClearSessionState(host);
      EmitState(host, L"idle");
      break;
    case RestoreIntent::quit:
      ClearSessionState(host);
      Emit(L"{\"type\":\"shutdown-complete\"}");
      g_done = true;
      break;
    case RestoreIntent::none:
      break;
  }
}

bool RequestRestore(Host& host, RestoreIntent intent, const std::wstring& failureReason = {}) {
  host.lastRestoredWindow = host.mainWindow;
  host.lastRestoredPid = host.mainPid;
  host.lastRestoredStyle = host.mainState.style;
  host.lastRestoreWindowGone = false;
  host.restoreFailureReason = failureReason;
  if (RestoreAll(host)) {
    CompleteRestoreIntent(host, intent);
    return true;
  }
  host.restoreIntent = intent;
  host.nextRestoreAttempt = GetTickCount64() + 1000;
  Emit(L"{\"type\":\"restore-pending\",\"reason\":\"" + JsonEscape(failureReason) +
    L"\",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L"}");
  EmitState(host, L"restore-failed", host.mainPid, host.mainWindow);
  if (intent == RestoreIntent::quit) Emit(L"{\"type\":\"shutdown-blocked\",\"reason\":\"A live external window could not be restored; the host and Electron window must remain alive.\"}");
  return false;
}

bool ClearSession(Host& host) {
  if (!RequestRestore(host, RestoreIntent::clearSession, L"Session cleanup could not restore its external window.")) return false;
  return true;
}

void Fallback(Host& host, const std::wstring& reason) {
  if (RequestRestore(host, RestoreIntent::fallback, reason)) {
    Emit(L"{\"type\":\"fallback-reason\",\"reason\":\"" + JsonEscape(reason) + L"\"}");
  }
}

void RetryRestore(Host& host) {
  if (host.restoreIntent == RestoreIntent::none || GetTickCount64() < host.nextRestoreAttempt) return;
  const RestoreIntent intent = host.restoreIntent;
  if (RestoreAll(host)) CompleteRestoreIntent(host, intent);
  else {
    host.nextRestoreAttempt = GetTickCount64() + 1000;
    EmitState(host, L"restore-failed", host.mainPid, host.mainWindow);
    if (intent == RestoreIntent::quit) Emit(L"{\"type\":\"shutdown-blocked\",\"reason\":\"External window restoration is still failing; retrying safely.\"}");
  }
}

void ProcessCommand(Host& host, const std::wstring& line) {
  std::vector<std::wstring> fields;
  std::wistringstream stream(line);
  std::wstring part;
  while (std::getline(stream, part, L'\t')) fields.push_back(part);
  if (fields.empty()) return;
  const auto number = [&](size_t index, int fallback) { try { return index < fields.size() ? std::stoi(fields[index]) : fallback; } catch (...) { return fallback; } };
  const std::wstring& command = fields[0];
  if (command == L"TEST_FAIL_RESTORE_ONCE" && fields.size() >= 2) {
    wchar_t enabled[8]{};
    if (GetEnvironmentVariableW(L"VIGIL_P15_TEST_FAULT_INJECTION", enabled, static_cast<DWORD>(std::size(enabled))) && std::wstring(enabled) == L"1") {
      host.failNextRestoreStage = fields[1];
      Emit(L"{\"type\":\"test-fault-armed\",\"stage\":\"" + JsonEscape(fields[1]) + L"\"}");
    } else EmitError(L"test-fault", ERROR_ACCESS_DENIED, L"Fault injection is disabled unless VIGIL_P15_TEST_FAULT_INJECTION=1.");
    return;
  }
  if (command == L"START" && fields.size() >= 7) {
    const std::wstring exe = Base64ToWide(fields[1]);
    const std::wstring args = Base64ToWide(fields[2]);
    if (exe.empty()) { EmitError(L"start", ERROR_INVALID_PARAMETER, L"The EXE path is empty or invalid UTF-8."); return; }
    if (host.launched && RefreshProcesses(host).empty() && !AdoptProcessesInDirectory(host, host.exePath)) {
      Emit(L"{\"type\":\"exited\",\"reason\":\"all-target-processes-exited\"}");
      if (!ClearSession(host)) return;
    }
    if (host.restoreIntent != RestoreIntent::none) {
      EmitError(L"start", ERROR_BUSY, L"The previous window has not been safely restored. Keep Vigil open and retry after recovery completes.");
      EmitState(host, L"restore-failed", host.mainPid, host.mainWindow);
      return;
    }
    if (host.launched && _wcsicmp(host.exePath.c_str(), exe.c_str()) == 0) {
      const auto activePids = RefreshProcesses(host);
      if (activePids.empty()) {
        Emit(L"{\"type\":\"exited\",\"reason\":\"all-target-processes-exited\"}");
        if (!ClearSession(host)) return;
      } else {
        if (host.attached && !IsWindow(host.mainWindow)) {
          const HWND lost = host.mainWindow;
          host.mainWindow = nullptr; host.mainState = {}; host.mainPid = 0; host.attached = false;
          host.discoveryStartedAt = GetTickCount64(); host.windowLostAt = host.discoveryStartedAt;
          Emit(L"{\"type\":\"window-lost\",\"reason\":\"hwnd-destroyed\",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(lost)) + L"}");
          EmitState(host, L"window-rebuilding");
        }
        UpdateBounds(host, number(3, 0), number(4, 0), number(5, 1), number(6, 1));
        host.visible = true;
        ApplyVisibility(host);
        if (host.detachedByUser) {
          host.detachedByUser = false; host.fallback = false;
          host.discoveryStartedAt = GetTickCount64(); host.windowLostAt = 0;
          EmitState(host, L"attaching");
        } else if (host.attached && IsWindow(host.mainWindow) && GetParent(host.mainWindow) == host.container) {
          Emit(L"{\"type\":\"start-reused\",\"pid\":" + std::to_wstring(host.launchPid) + L",\"windowPid\":" +
            std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(host.mainWindow)) + L"}");
          Emit(StateJson(host.targetWindowHidden ? L"window-hidden" : L"attached", host.launchPid, host.mainPid, host.mainWindow));
        } else if (host.fallback) {
          Emit(L"{\"type\":\"start-reused\",\"pid\":" + std::to_wstring(host.launchPid) + L",\"windowPid\":0,\"hwnd\":0}");
          Emit(StateJson(L"fallback", host.launchPid));
        } else if (host.windowLostAt) {
          EmitState(host, L"window-rebuilding");
        } else if (host.state == L"attaching") {
          EmitState(host, L"attaching");
        } else {
          EmitState(host, L"starting");
        }
        return;
      }
    }
    if (host.launched && _wcsicmp(host.exePath.c_str(), exe.c_str()) != 0) {
      const auto activePids = RefreshProcesses(host);
      const auto remainingWindows = FindWindows(activePids, host.container);
      if (host.detachedByUser || (host.fallback && remainingWindows.empty())) {
        const DWORD releasedPid = host.launchPid;
        if (!ClearSession(host)) {
          EmitError(L"start", ERROR_BUSY, L"The previous window could not be safely restored; the new profile was not started.");
          return;
        }
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
  if (command == L"SHOW") { host.visible = true; ApplyVisibility(host); return; }
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
  if (command == L"ATTACH") {
    if (host.restoreIntent != RestoreIntent::none) {
      EmitError(L"attach", ERROR_BUSY, L"The previous window is still being safely restored.");
      return;
    }
    if (host.attached && IsWindow(host.mainWindow) && GetParent(host.mainWindow) == host.container) {
      EmitState(host, L"attached", host.mainPid, host.mainWindow);
      return;
    }
    if (host.launched && !host.attached && !host.detachedByUser && !host.fallback &&
        (host.state == L"starting" || host.state == L"window-rebuilding")) {
      EmitState(host, host.state);
      return;
    }
    host.fallback = false; host.detachedByUser = false; host.discoveryStartedAt = GetTickCount64();
    EmitState(host, L"attaching");
    return;
  }
  if (command == L"DETACH") {
    if (!host.mainWindow) {
      DWORD currentPid = 0;
      const bool priorWindowAlive = IsWindow(host.lastRestoredWindow) &&
        GetWindowThreadProcessId(host.lastRestoredWindow, &currentPid) && currentPid == host.lastRestoredPid;
      host.detachedByUser = host.launched;
      host.fallback = false;
      Emit(L"{\"type\":\"detached\",\"restored\":" + std::wstring(priorWindowAlive ? L"true" : L"false") +
        L",\"windowGone\":" + (priorWindowAlive ? L"false" : L"true") + L",\"alreadyDetached\":" +
        (priorWindowAlive ? L"true" : L"false") + L",\"hwnd\":" +
        std::to_wstring(reinterpret_cast<uintptr_t>(priorWindowAlive ? host.lastRestoredWindow : nullptr)) + L"}");
      EmitState(host, L"detached");
      return;
    }
    if (host.restoreIntent != RestoreIntent::none) {
      EmitError(L"detach", ERROR_BUSY, L"A window restore is already pending; the helper will continue retrying it.");
      return;
    }
    RequestRestore(host, RestoreIntent::detach, L"User requested detach.");
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
  if (command == L"QUIT") {
    if (host.restoreIntent != RestoreIntent::none) {
      if (host.restoreIntent != RestoreIntent::quit) host.restoreIntent = RestoreIntent::quit;
      Emit(L"{\"type\":\"shutdown-blocked\",\"reason\":\"Waiting for the outstanding window restoration to succeed.\"}");
      return;
    }
    RequestRestore(host, RestoreIntent::quit, L"Application shutdown requested.");
    return;
  }
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
  g_inputClosed = true;
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
    if (g_inputClosed && host.restoreIntent == RestoreIntent::none) RequestRestore(host, RestoreIntent::quit, L"Electron control channel closed.");
    RetryRestore(host);
    if (g_done) break;

    MSG message{};
    while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }

    if (!g_done && !IsWindow(parent) && host.restoreIntent == RestoreIntent::none) RequestRestore(host, RestoreIntent::quit, L"Electron parent window was destroyed.");
    if (IsWindow(parent) && host.launched && host.restoreIntent == RestoreIntent::none) {
      const auto pids = RefreshProcesses(host);
      if (pids.empty()) {
        // 启动器退出后进程族会断链，但目标软件可能已经在跑（启动器把参数交给了它）。
        // 先把同目录进程收回托管，实在找不到才算真的退出。
        if (AdoptProcessesInDirectory(host, host.exePath)) {
          Emit(L"{\"type\":\"adopted-existing\",\"reason\":\"launcher-exited\",\"launchPid\":" +
            std::to_wstring(host.launchPid) + L",\"tracked\":" + std::to_wstring(host.trackedProcesses.size()) + L"}");
          host.discoveryStartedAt = GetTickCount64();
        } else {
          const DWORD oldPid = host.launchPid;
          if (ClearSession(host)) Emit(L"{\"type\":\"exited\",\"pid\":" + std::to_wstring(oldPid) + L",\"reason\":\"all-target-processes-exited\"}");
        }
      } else {
        if (host.attached && !IsWindow(host.mainWindow)) {
          host.mainWindow = nullptr; host.mainPid = 0; host.attached = false;
          host.discoveryStartedAt = GetTickCount64();
      host.windowLostAt = GetTickCount64();
      Emit(L"{\"type\":\"window-lost\",\"reason\":\"hwnd-destroyed\"}");
      EmitState(host, L"window-rebuilding");
        }
        if (host.attached && IsWindow(host.mainWindow)) {
          // 软件会自己改尺寸（记住最大化、按项目恢复布局），偏离绑定位就拉回来。
          if (host.visible) {
            RECT actual{};
            if (GetWindowRect(host.mainWindow, &actual)) {
              const int wantWidth = static_cast<int>(host.bounds.right - host.bounds.left);
              const int wantHeight = static_cast<int>(host.bounds.bottom - host.bounds.top);
              const int actualWidth = static_cast<int>(actual.right - actual.left);
              const int actualHeight = static_cast<int>(actual.bottom - actual.top);
              if (std::abs(static_cast<int>(actual.left) - static_cast<int>(host.bounds.left)) > 4 ||
                  std::abs(static_cast<int>(actual.top) - static_cast<int>(host.bounds.top)) > 4 ||
                  std::abs(actualWidth - wantWidth) > 4 || std::abs(actualHeight - wantHeight) > 4) {
                if (IsZoomed(host.mainWindow)) ShowWindow(host.mainWindow, SW_RESTORE);
                SetWindowPos(host.mainWindow, nullptr, host.bounds.left, host.bounds.top, wantWidth, wantHeight,
                  SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
              }
            }
          }
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
              ApplyVisibility(host);
              RECT attachedRect{}; GetWindowRect(windows.front().hwnd, &attachedRect);
              Emit(L"{\"type\":\"attached\",\"pid\":" + std::to_wstring(host.mainPid) + L",\"hwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(windows.front().hwnd)) + L",\"parentHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(GetParent(windows.front().hwnd))) + L",\"ownerHwnd\":" + std::to_wstring(reinterpret_cast<uintptr_t>(GetWindow(windows.front().hwnd, GW_OWNER))) + L",\"dpi\":" + std::to_wstring(GetDpiForWindow(windows.front().hwnd)) + L",\"screenRect\":[" + std::to_wstring(attachedRect.left) + L"," + std::to_wstring(attachedRect.top) + L"," + std::to_wstring(attachedRect.right) + L"," + std::to_wstring(attachedRect.bottom) + L"]}");
              EmitState(host, L"attached", host.mainPid, host.mainWindow);
            }
          } else if (host.windowLostAt && GetTickCount64() - host.windowLostAt > kWindowRebuildGraceMs) {
            const DWORD oldPid = host.launchPid;
            const size_t liveCount = pids.size();
            Emit(L"{\"type\":\"window-closed-resident\",\"pid\":" + std::to_wstring(oldPid) + L",\"liveProcessCount\":" + std::to_wstring(liveCount) + L",\"processEnded\":false}");
            EmitState(host, L"resident", 0, nullptr);
            ClearSession(host);
          } else if (!host.windowLostAt && GetTickCount64() - host.discoveryStartedAt > 30000) {
            Fallback(host, L"No visible top-level window was found in the launched process family within 30 seconds; the process remains running independently.");
          }
        }
      }
    }
    Sleep(kPollMs);
  }
  if (!host.mainWindow && IsWindow(container)) DestroyWindow(container);
  else if (host.mainWindow && IsWindow(host.mainWindow)) Emit(L"{\"type\":\"container-preserved\",\"reason\":\"A valid external HWND is still associated with this container.\"}");
  if (reader.joinable()) { if (g_done) reader.detach(); else reader.join(); }
  return 0;
}
