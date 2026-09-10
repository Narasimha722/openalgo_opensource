# Electrobun Complete API Reference

## Imports

```typescript
// Bun-side (main process)
import {
  BrowserWindow, BrowserView, ApplicationMenu, ContextMenu,
  Tray, Updater, Utils, BuildConfig, Screen, Session,
  GlobalShortcut, Socket, PATHS,
} from "electrobun/bun";
import type { RPCSchema } from "electrobun/bun";

// Webview-side (browser)
import { Electroview } from "electrobun/view";
import type { RPCSchema } from "electrobun/view";

// Global events
import Electrobun from "electrobun/bun";
Electrobun.events.on("event-name", handler);
```

## BrowserWindow

### Constructor

```typescript
const win = new BrowserWindow({
  title: "Window Title",                    // Default: "Electrobun"
  url: "views://mainview/index.html",       // views:// or https://
  html: "<html>...</html>",                 // Alternative: raw HTML
  preload: "views://mypreload.js",          // Script before page loads
  renderer: "native",                       // "native" | "cef"
  frame: { x: 100, y: 100, width: 800, height: 600 },
  titleBarStyle: "default",                 // "default" | "hidden" | "hiddenInset"
  transparent: false,
  sandbox: false,                           // true disables RPC
  navigationRules: "views://*",             // Glob-based URL rules
  rpc: rpcObject,                           // From BrowserView.defineRPC()
  styleMask: {                              // macOS window style
    Borderless: false, Titled: true, Closable: true,
    Miniaturizable: true, Resizable: true,
    FullSizeContentView: false, UtilityWindow: false,
  },
});
```

### Properties

- `win.id` — Unique window ID (number)
- `win.title` — Window title (string)
- `win.frame` — {x, y, width, height}
- `win.url` — Initial URL
- `win.webview` — Default BrowserView instance
- `win.webviewId` — Default webview ID

### Methods

```typescript
win.setTitle(title: string): void
win.close(): void
win.focus(): void
win.show(): void
win.minimize(): void
win.unminimize(): void
win.isMinimized(): boolean
win.maximize(): void
win.unmaximize(): void
win.isMaximized(): boolean
win.setFullScreen(fs: boolean): void
win.isFullScreen(): boolean
win.setAlwaysOnTop(top: boolean): void
win.isAlwaysOnTop(): boolean
win.setPosition(x: number, y: number): void
win.setSize(width: number, height: number): void
win.setFrame(x: number, y: number, w: number, h: number): void
win.getFrame(): { x, y, width, height }
win.getPosition(): { x, y }
win.getSize(): { width, height }
win.on(event: string, handler: Function): void

// Static
BrowserWindow.getById(id: number): BrowserWindow
```

### Events

| Event | Data |
|-------|------|
| `close` | `{ id }` |
| `resize` | `{ id, x, y, width, height }` |
| `move` | `{ id, x, y }` |
| `focus` | `{ id }` |

---

## BrowserView

### Constructor (usually auto-created by BrowserWindow)

```typescript
const view = new BrowserView({
  url: "views://page/index.html",
  html: null,
  preload: null,
  renderer: "native",
  partition: "persist:trading",
  frame: { x: 0, y: 0, width: 800, height: 600 },
  rpc: rpcObject,
  sandbox: false,
  autoResize: true,
  navigationRules: "views://*,https://api.broker.com/*",
});
```

### Methods

```typescript
view.loadURL(url: string): void
view.loadHTML(html: string): void
view.executeJavascript(js: string): void     // Fire-and-forget
view.findInPage(text: string, opts?: { forward?: boolean; matchCase?: boolean }): void
view.stopFindInPage(): void
view.openDevTools(): void
view.closeDevTools(): void
view.toggleDevTools(): void
view.setNavigationRules(rules: string): void
view.on(event: string, handler: Function): void

// Built-in RPC method (available on all views with RPC)
const result = await view.rpc?.request.evaluateJavascriptWithResponse({
  script: "document.title"
});

// Static
BrowserView.getAll(): BrowserView[]
BrowserView.getById(id: number): BrowserView | undefined
BrowserView.defineRPC<T>(config): T
```

### Events

`will-navigate`, `did-navigate`, `did-navigate-in-page`, `did-commit-navigation`, `dom-ready`, `new-window-open`, `host-message`, `download-started`, `download-progress`, `download-completed`, `download-failed`

---

## RPC System

### Defining Schema

```typescript
type MyRPC = {
  bun: RPCSchema<{
    requests: {
      methodName: { params: ParamType; response: ResponseType };
    };
    messages: {
      messageName: PayloadType;
    };
  }>;
  webview: RPCSchema<{
    requests: {
      methodName: { params: ParamType; response: ResponseType };
    };
    messages: {
      messageName: PayloadType;
    };
  }>;
};
```

### Bun-Side

```typescript
const rpc = BrowserView.defineRPC<MyRPC>({
  maxRequestTime: 30000,  // Timeout in ms
  handlers: {
    requests: {
      methodName: async (params) => { return response; },
    },
    messages: {
      messageName: (payload) => { /* handle */ },
      "*": (name, payload) => { /* wildcard handler */ },
    },
  },
});

// Pass to window
const win = new BrowserWindow({ url: "...", rpc });

// Call webview methods
const result = await win.webview.rpc?.request.webviewMethod(params);

// Send messages to webview
win.webview.rpc?.send.messageName(payload);
```

### Webview-Side

```typescript
const rpc = Electroview.defineRPC<MyRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: { /* webview-side request handlers */ },
    messages: { /* handle messages from bun */ },
  },
});
const _ev = new Electroview({ rpc });

// Call bun methods
const result = await rpc.request.bunMethod(params);

// Send messages to bun
rpc.send.messageName(payload);
```

---

## ApplicationMenu

```typescript
ApplicationMenu.setApplicationMenu([
  {
    label: "App Name",
    submenu: [
      { role: "about" },
      { type: "separator" },
      { label: "Custom Item", action: "my-action", accelerator: "k", data: { key: "val" } },
      { label: "Disabled", action: "x", enabled: false },
      { label: "Checked", action: "y", checked: true },
      { label: "Hidden", action: "z", hidden: true },
      { role: "quit" },
    ],
  },
]);

// Listen for clicks
Electrobun.events.on("application-menu-clicked", (e) => {
  console.log(e.data.action, e.data.data);
});
```

### Menu Item Types

```typescript
{ type: "normal", label, action, accelerator, data, enabled, checked, hidden, tooltip, submenu }
{ type: "separator" }  // or "divider"
{ role: "about" | "quit" | "hide" | "copy" | "paste" | ... }
```

---

## ContextMenu

```typescript
ContextMenu.showContextMenu([
  { label: "Cut", action: "cut", accelerator: "x" },
  { label: "Copy", action: "copy", accelerator: "c" },
  { type: "separator" },
  { label: "Submenu", submenu: [
    { label: "Option A", action: "opt-a", data: { id: 1 } },
  ]},
]);

Electrobun.events.on("context-menu-clicked", (e) => {
  console.log(e.data.action, e.data.data);
});
```

---

## Tray

```typescript
const tray = new Tray({
  title: "My App",
  image: "views://icon.png",
  template: true,     // macOS template image
  width: 18,
  height: 18,
});

tray.setMenu([ /* same format as context menu */ ]);
tray.setTitle("New Title");
tray.setImage("views://new-icon.png");
tray.remove();

tray.on("tray-clicked", (e) => {
  console.log(e.data.action, e.data.data);
});

// Static
Tray.getById(id): Tray
Tray.getAll(): Tray[]
Tray.removeById(id): void
```

---

## Utils

```typescript
// File operations
Utils.moveToTrash(path: string): void
Utils.showItemInFolder(path: string): void
Utils.openPath(path: string): void
Utils.openExternal(url: string): void

// Notifications
Utils.showNotification({ title, body?, subtitle?, silent? }): void

// Message box
const result = await Utils.showMessageBox({
  type: "info" | "warning" | "error" | "question",
  title: string,
  message: string,
  detail?: string,
  buttons: string[],
  defaultId?: number,
  cancelId?: number,
});
// result.response = button index clicked

// File dialog
const paths = await Utils.openFileDialog({
  startingFolder?: string,
  allowedFileTypes?: string,       // "ts,js,json"
  canChooseFiles?: boolean,
  canChooseDirectory?: boolean,
  allowsMultipleSelection?: boolean,
});

// Clipboard
Utils.clipboardWriteText(text: string): void
Utils.clipboardReadText(): string | null
Utils.clipboardReadImage(): Uint8Array | null
Utils.clipboardWriteImage(png: Uint8Array): void
Utils.clipboardAvailableFormats(): string[]
Utils.clipboardClear(): void

// Lifecycle
Utils.quit(): void
```

### Utils.paths (System Directories)

```typescript
Utils.paths.home         // Home directory
Utils.paths.appData      // ~/Library/Application Support (mac)
Utils.paths.config       // ~/Library/Preferences (mac)
Utils.paths.cache        // ~/Library/Caches (mac)
Utils.paths.temp         // System temp
Utils.paths.logs         // ~/Library/Logs (mac)
Utils.paths.documents    // ~/Documents
Utils.paths.downloads    // ~/Downloads
Utils.paths.desktop      // ~/Desktop
Utils.paths.pictures     // ~/Pictures
Utils.paths.music        // ~/Music
Utils.paths.videos       // ~/Movies (mac)
Utils.paths.userData     // {appData}/{identifier}/{channel}
Utils.paths.userCache    // {cache}/{identifier}/{channel}
Utils.paths.userLogs     // {logs}/{identifier}/{channel}
```

---

## PATHS (Resource Paths)

```typescript
import { PATHS } from "electrobun/bun";

PATHS.RESOURCES_FOLDER   // app Resources directory (read-only)
PATHS.VIEWS_FOLDER       // app views directory
```

---

## GlobalShortcut

```typescript
GlobalShortcut.register("CommandOrControl+Shift+I", () => { /* handler */ }): boolean
GlobalShortcut.isRegistered("CommandOrControl+Shift+I"): boolean
GlobalShortcut.unregister("CommandOrControl+Shift+I"): void
GlobalShortcut.unregisterAll(): void
```

**Accelerator format:** `Modifier+Key` where modifiers are `Command`, `Control`, `CommandOrControl`, `Alt`, `Shift`, `Super`. Keys: A-Z, 0-9, F1-F12, Space, Enter, Tab, Escape, Backspace, Delete, arrows, etc.

---

## Screen

```typescript
Screen.getPrimaryDisplay(): Display
// { id, bounds: {x,y,width,height}, workArea: {x,y,width,height}, scaleFactor, isPrimary }

Screen.getAllDisplays(): Display[]

Screen.getCursorScreenPoint(): { x: number; y: number }
```

---

## Session

```typescript
const session = Session.fromPartition("persist:trading");
const defaultSession = Session.defaultSession;

// Cookies
await session.cookies.set({ name, value, domain, path, secure, httpOnly, sameSite, expirationDate });
const cookies = await session.cookies.get({ url?, name?, domain?, path?, secure?, session? });
await session.cookies.remove(url: string, name: string);
await session.cookies.clear();

// Storage
await session.clearStorageData(["localStorage", "cache", "cookies", "indexedDB", "all"]);
```

---

## Updater

```typescript
// Local info
await Updater.localInfo.version(): string
await Updater.localInfo.channel(): string  // "dev" | "canary" | "stable"
await Updater.localInfo.hash(): string

// Paths
Updater.appDataFolder(): string
Updater.channelBucketUrl(): string

// Update lifecycle
const info = await Updater.checkForUpdate();
// { version, hash, updateAvailable, updateReady, error }

await Updater.downloadUpdate();
await Updater.applyUpdate();  // Quits, replaces binary, relaunches

// Status tracking
Updater.onStatusChange((entry: UpdateStatusEntry) => { });
Updater.getStatusHistory(): UpdateStatusEntry[]
Updater.clearStatusHistory(): void
```

---

## BuildConfig

```typescript
const config = await BuildConfig.get();
// { defaultRenderer, availableRenderers, cefVersion?, bunVersion?, runtime? }

const cached = BuildConfig.getCached(); // Sync, may be null
```

---

## Global Events

```typescript
import Electrobun from "electrobun/bun";

// Application events
Electrobun.events.on("application-menu-clicked", (e) => { });
Electrobun.events.on("context-menu-clicked", (e) => { });
Electrobun.events.on("open-url", (e) => { });          // URL scheme (macOS)
Electrobun.events.on("before-quit", async (e) => { });  // Cancellable

// Global window events (all windows)
Electrobun.events.on("close", (e) => { });
Electrobun.events.on("resize", (e) => { });
Electrobun.events.on("move", (e) => { });
Electrobun.events.on("focus", (e) => { });

// Global webview events (all webviews)
Electrobun.events.on("will-navigate", (e) => { });
Electrobun.events.on("did-navigate", (e) => { });
Electrobun.events.on("dom-ready", (e) => { });
Electrobun.events.on("new-window-open", (e) => { });

// Cancel navigation
Electrobun.events.on("will-navigate", (e) => {
  if (e.data.detail.includes("blocked.com")) {
    e.response = { allow: false };
  }
});

// Remove listener
Electrobun.events.off("event-name", handler);
```

---

## Navigation Rules

```typescript
// Comma-separated glob patterns. ^ prefix = block. Last match wins.
const rules = "views://*,https://api.broker.com/*,^https://evil.com/*,^*";

// On window creation
new BrowserWindow({ navigationRules: rules });

// Or dynamically
webview.setNavigationRules(rules);
```

---

## Webview Tag (Browser-Side)

```html
<electrobun-webview
  src="https://example.com"
  preload="views://preload.js"
  renderer="native"
  partition="persist:browsing"
  sandbox
  style="width: 100%; height: 400px;">
</electrobun-webview>
```

```typescript
const wv = document.querySelector("electrobun-webview");
wv.loadURL(url);
wv.loadHTML(html);
wv.goBack();
wv.goForward();
wv.reload();
await wv.canGoBack();
await wv.canGoForward();
wv.findInPage(text, opts);
wv.stopFindInPage();
wv.openDevTools();
wv.toggleDevTools();
wv.setNavigationRules(rules);
wv.addMaskSelector(".toolbar");
wv.removeMaskSelector(".toolbar");
wv.on("dom-ready", handler);
wv.off("dom-ready", handler);
```

---

## Draggable Regions

```html
<!-- CSS class approach -->
<div class="electrobun-webkit-app-region-drag">Custom Titlebar</div>

<!-- Inline style approach -->
<div style="app-region: drag;">Custom Titlebar</div>
```

---

## CLI Commands

```bash
electrobun init [name] [--template=hello-world|react-tailwind-vite|photo-booth|multitab-browser|svelte]
electrobun build [--env=dev|canary|stable]
electrobun dev
```

---

## Platform Support

| Feature | macOS | Windows | Linux |
|---------|-------|---------|-------|
| Native Renderer | WKWebView | WebView2 | WebKitGTK |
| CEF Renderer | Optional | Optional | Recommended |
| App Menu | Full | Full | Limited |
| Context Menu | Full | Simple | Not supported |
| Tray | Full | Full | Full |
| Global Shortcuts | Full | Full | Full |
| Code Signing | Full + Notarize | N/A | N/A |
| URL Schemes | Full | N/A | N/A |
