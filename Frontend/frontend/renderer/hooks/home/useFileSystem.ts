import { useState, useCallback, useEffect, useRef } from 'react';
import axios from 'axios';
import { FileEntry, ExportModalState, UserData } from '../../components/home/types';
import { PendingFile } from '../../components/home/FileCreationApproval';
import { splitCodeIntoFiles } from '../../components/home/export-utils';
import { confirmDialog } from '../../components/ui/ConfirmDialog';
import { cevir } from '../../lib/i18n';
import { backendWorkspacePath, hostWorkspacePath } from '../../lib/backendWorkspacePath';
import { routeForFile } from '../../components/model-viewer/extensions';

const ipc = typeof window !== 'undefined' ? (window as any).ipc : null;

// One file reaches this hook under several legitimate spellings: the tree hands
// out absolute host paths, an approved agent operation names the file relative
// to the workspace, and Windows accepts either separator for the same entry.
// Comparing the raw strings made those spellings look like unrelated files, so
// a successful delete could leave the content area on a file that is gone.
//
// The case rule is decided by the SPELLING, not by the process this runs in:
// the renderer has no `process.platform`, and a drive-lettered or UNC path
// names a Windows filesystem no matter where the comparison happens, while a
// POSIX path must keep its case significant. That is the whole platform test.
const windowsSpelled = (p: string) => /^[A-Za-z]:[\\/]/.test(p) || p.startsWith('\\\\');

const absoluteYol = (p: string) =>
  p.startsWith('/') || p.startsWith('\\') || /^[A-Za-z]:[\\/]/.test(p);

// `.` and `..` name no entry of their own, so `/a/Assets/../hero.fbx` and
// `/a/hero.fbx` are ONE file: a delete of either spelling makes the other
// untrue. Resolution is pure string work on the already-separator-normalized
// form — the renderer has no filesystem to ask, and a comparator that performed
// I/O would be the wrong shape regardless of where it ran.
//
// A `..` that climbs past the ROOT of an absolute path is absorbed, because that
// is what the filesystem itself resolves: `/..` is `/` and `C:\..` is `C:\`, so
// `/a/../../hero.fbx` and `/hero.fbx` really are the same entry. A `..` still
// leading a RELATIVE path is kept instead — with no base to climb from, dropping
// it would equate `../hero.fbx` with `hero.fbx`, which name files in different
// directories. A relative path only survives this far when no workspace is
// selected; otherwise it was already joined onto one above.
const resolveDotSegments = (duz: string, mutlakMi: boolean) => {
  if (!/(^|\/)\.\.?(\/|$)/.test(duz)) return duz;
  // An absolute path's first segment is its root token — `''` for `/a/b`, `C:`
  // for `C:/a/b` — and is never popped: it is what the climb stops at.
  const kokAdedi = mutlakMi ? 1 : 0;
  const yigin: string[] = [];
  for (const parca of duz.split('/')) {
    if (parca === '.') continue;
    if (parca === '..') {
      if (yigin.length > kokAdedi && yigin[yigin.length - 1] !== '..') yigin.pop();
      else if (!mutlakMi) yigin.push('..');
      continue;
    }
    yigin.push(parca);
  }
  return yigin.join('/');
};

// COMPARISON ONLY. Nothing derived from this is stored or displayed: the paths
// the user sees keep the casing and separators the main process reported.
const canonicalPath = (p: string, workspacePath: string | null) => {
  const mutlak = absoluteYol(p) || !workspacePath ? p : `${workspacePath}/${p}`;
  const duz = mutlak.replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/\/+$/, '');
  const cozulmus = resolveDotSegments(duz, absoluteYol(mutlak));
  return windowsSpelled(mutlak) ? cozulmus.toLowerCase() : cozulmus;
};

// Deleting or renaming a FOLDER takes every path under it with it, so the
// content area has to react to an ancestor as well as to the exact file. The
// separator is required, so `/a/Assets` covers `/a/Assets/hero.fbx` but never
// the sibling `/a/AssetsOld/hero.fbx`.
const containsPath = (p: string, hedef: string) => p === hedef || p.startsWith(hedef + '/');

// A result that arrives late must not overwrite state a NEWER user action
// already owns. Two independent domains here need that check — which workspace
// is selected, and who owns the shared content area — so they share this
// mechanism but deliberately not one counter: a single counter would make
// opening a file cancel a pending workspace selection, which is not a race,
// just two unrelated things happening at once.
const useLatestRequest = () => {
  const seq = useRef(0);
  // What the current owner is waiting for, when the owner can name it. It lets
  // a caller cancel the in-flight request SELECTIVELY instead of cancelling
  // every pending request whenever anything changes.
  const konu = useRef<string | null>(null);
  return useRef({
    // Become the owner; the returned predicate reports whether still the owner.
    claim: (bekleyen: string | null = null) => {
      const mine = ++seq.current;
      konu.current = bekleyen;
      return () => seq.current === mine;
    },
    // The path the owner is waiting for, or null when it has none to name.
    subject: () => konu.current,
    // The owner announcing that its request is no longer in flight. Only the
    // current owner calls it, so it cannot erase a newer claim's subject.
    settle: () => { konu.current = null; },
    // Watch the CURRENT owner without becoming it. Background readers — tree
    // refresh, directory expansion, git status, last-workspace restore — must be
    // droppable by a newer action, but claiming would make each of them cancel a
    // selection that is already in flight, which is the opposite defect: a
    // refresh is not a decision about which workspace is open.
    observe: () => { const seen = seq.current; return () => seq.current === seen; },
    // Become the owner without an async continuation of one's own — used by the
    // synchronous actions (close, switch, preview) that supersede pending work.
    invalidate: () => { seq.current += 1; konu.current = null; },
  }).current;
};

export const useFileSystem = (API: string, user: UserData | null, showToast: (msg: string, type: any) => void) => {
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [lastWorkspacePath, setLastWorkspacePath] = useState<string | null>(null);
  const [rootFolderPath, setRootFolderPath] = useState<string | null>(null);
  const [fileTree, setFileTree] = useState<FileEntry[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [dirContents, setDirContents] = useState<Record<string, FileEntry[]>>({});
  const [openedFilePath, setOpenedFilePath] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [originalCode, setOriginalCode] = useState('');
  const [isDirty, setIsDirty] = useState(false);
  // The editor and the 3D preview share one content area, so at most one of
  // `openedFilePath` / `previewFile` may be set. The invariant lives here
  // because both are written here.
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);

  // The editor path as it is RIGHT NOW, for the async continuations that must
  // not answer from the render they were created in. Assigned during render
  // rather than from an effect on purpose: an IPC reply is a promise
  // continuation, and passive effects are not guaranteed to have flushed by the
  // time one runs, so an effect-written ref could still hold the previous path.
  const openedFilePathRef = useRef(openedFilePath);
  openedFilePathRef.current = openedFilePath;

  const workspaceRequest = useLatestRequest();
  const contentRequest = useLatestRequest();

  const openPreview = useCallback((filePath: string) => {
    contentRequest.invalidate();
    setOpenedFilePath(null);
    setCode('');
    setOriginalCode('');
    setPreviewFile({ path: filePath, name: filePath.split(/[\\/]/).pop() || filePath });
  }, [contentRequest]);

  const closePreview = useCallback(() => setPreviewFile(null), []);

  // Both comparators resolve their arguments against the CURRENT workspace, so
  // a relative operation path and an absolute shown path meet in one form.
  const yolEtkilendi = useCallback(
    (p: string, hedef: string) =>
      containsPath(canonicalPath(p, workspacePath), canonicalPath(hedef, workspacePath)),
    [workspacePath],
  );
  const samePath = useCallback(
    (p: string, hedef: string) => canonicalPath(p, workspacePath) === canonicalPath(hedef, workspacePath),
    [workspacePath],
  );

  // ONE place where a filesystem path change reaches the content area. Five
  // earlier fixes each closed one path — direct delete, tree delete, rename,
  // workspace switch, missing replacement workspace — and each time a later
  // audit found another operation nobody had enumerated (the tree move was the
  // sixth). The rule is therefore not repeated per call site any more: an
  // operation that renames, moves or removes a path calls this, and an
  // operation that does not is the odd one out.
  //
  // `newPath === null` means the path is gone. A string means the main-process
  // handler reported exactly where the entry went, which is the same string the
  // file readers accept back, so the content area follows it rather than
  // emptying — losing the open model on a move would be the worse answer.
  // A path merely UNDER the changed entry (a file inside a renamed or moved
  // folder) is cleared: no handler reports the new path of each descendant, so
  // there is nothing truthful to follow it to.
  const applyContentPathChange = useCallback((oldPath: string, newPath: string | null) => {
    // A read of this very path may still be in flight; without this it lands
    // afterwards and reopens the path the operation just invalidated. Only a
    // read of an AFFECTED path is dropped — cancelling every pending read on
    // any path change would throw away a legitimate open of another file.
    const bekleyen = contentRequest.subject();
    if (bekleyen && yolEtkilendi(bekleyen, oldPath)) contentRequest.invalidate();
    setPreviewFile(prev => {
      if (!prev || !yolEtkilendi(prev.path, oldPath)) return prev;
      if (!samePath(prev.path, oldPath) || !newPath) return null;
      // The new name decides the route again: a preview renamed to a text
      // extension has no panel to show it, and following it kept the content
      // area in preview mode over a text path (found by a verification probe).
      if (routeForFile(newPath) === 'text') return null;
      return { path: newPath, name: newPath.split(/[\\/]/).pop() || newPath };
    });
    // Read through the ref, not the closure. A read that began while this
    // operation was in flight can COMMIT before the operation's answer arrives,
    // and it does so after this callback was created, so the captured
    // `openedFilePath` would still say the editor was empty and the file the
    // operation removed would stay on screen. `previewFile` above is safe for
    // the same reason it is written through an updater: both need the value as
    // it is now, not as it was when the operation started.
    const acikYol = openedFilePathRef.current;
    if (acikYol && yolEtkilendi(acikYol, oldPath)) {
      // Same rule in the other direction: an editor buffer renamed to a binary
      // extension would otherwise keep showing text for a path the readers now
      // route to a preview channel.
      if (samePath(acikYol, oldPath) && newPath && routeForFile(newPath) === 'text') {
        setOpenedFilePath(newPath);
      } else {
        setOpenedFilePath(null);
        setCode('');
        setOriginalCode('');
      }
    }
  }, [yolEtkilendi, samePath, contentRequest]);

  // What a path operation must still be true for when its IPC answers.
  //
  // NOT who owns the content area. Gating on that made an operation already IN
  // FLIGHT lose to a read that merely STARTED later: opening the very file a
  // delete was about to remove took ownership, so the successful delete skipped
  // `applyContentPathChange` altogether and the late read reopened a file that
  // was gone. An operation is not a claim on the content area, and being
  // overtaken there is not a reason to leave the area naming a path the
  // operation just made untrue.
  //
  // What CAN make an operation's path meaningless is the workspace changing
  // under it — the same relative path then names a different, still-present
  // file — so that is what these operations observe, and only that. Which
  // content a successful operation invalidates is decided by the path itself
  // inside `applyContentPathChange`, which is why a read of another file still
  // commits while this one is deleted.
  const pathOperationScope = useCallback(() => workspaceRequest.observe(), [workspaceRequest]);

  // VSCode tarzı git rozetleri: mutlak yol → durum (modified/added/untracked/deleted)
  const [gitStatus, setGitStatus] = useState<{ isRepo: boolean; files: Record<string, string>; dirs: Record<string, string> }>({ isRepo: false, files: {}, dirs: {} });
  const refreshGitStatus = useCallback(async (ws?: string | null) => {
    const target = ws ?? workspacePath;
    if (!ipc || !target) return;
    // The badges describe ONE workspace. A status computed for the workspace the
    // user just left repaints the new tree with the old repo's file states, and
    // nothing on screen says the colours belong to another project.
    const halaGecerli = workspaceRequest.observe();
    try {
      const res = await ipc.invoke('git-status', target);
      if (!halaGecerli()) return;
      setGitStatus(res || { isRepo: false, files: {}, dirs: {} });
    } catch { /* best-effort */ }
  }, [workspacePath, workspaceRequest]);

  // Periyodik tazeleme (20 sn) — dışarıda (IDE/terminal) yapılan değişiklikler de yansısın
  useEffect(() => {
    if (!workspacePath) { setGitStatus({ isRepo: false, files: {}, dirs: {} }); return; }
    refreshGitStatus(workspacePath);
    const iv = setInterval(() => refreshGitStatus(workspacePath), 20000);
    return () => clearInterval(iv);
  }, [workspacePath, refreshGitStatus]);
  
  // Track dirty state
  useEffect(() => {
    setIsDirty(code !== originalCode);
  }, [code, originalCode]);

  const saveFile = useCallback(async () => {
    if (!ipc || !openedFilePath || !workspacePath) return false;
    const halaGecerli = contentRequest.observe();
    const res = await ipc.invoke('write-file', openedFilePath, code, workspacePath);
    if (res?.success) {
      // The write itself succeeded and is still reported as such, but the
      // baseline it establishes belongs to the file that was open when the save
      // started: applying it to a file opened since would mark that other file's
      // unsaved text as already on disk.
      if (halaGecerli()) {
        setOriginalCode(code);
        setIsDirty(false);
      }
      showToast(cevir('file.saved'), 'success');
      return true;
    } else {
      showToast(cevir('file.saveError', { hata: res?.error || cevir('common.unknown') }), 'error');
      return false;
    }
  }, [code, openedFilePath, workspacePath, showToast, contentRequest]);

  const [treeContextMenu, setTreeContextMenu] = useState<{ x: number; y: number; entry: FileEntry } | null>(null);
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [treeCreating, setTreeCreating] = useState<{ parentPath: string; type: 'file' | 'folder' } | null>(null);
  const [treeCreateValue, setTreeCreateValue] = useState('');
  const [treeDragSource, setTreeDragSource] = useState<FileEntry | null>(null);
  const [treeDragTarget, setTreeDragTarget] = useState<string | null>(null);

  const [exportModal, setExportModal] = useState<ExportModalState | null>(null);
  const [exportFileName, setExportFileName] = useState('');
  
  // Pending Actions
  const [pendingGenFiles, setPendingGenFiles] = useState<{ files: PendingFile[]; messageId: number } | null>(null);
  // One card on screen, but requests do not come one at a time: with parallel
  // tool calls a second delete request used to erase the first card, which
  // then went unanswered until its gate timed out (30 Aug 2026 audit). So the
  // slot is the head of a queue.
  type DeleteCard = { path: string; messageId: number };
  const [pendingDeletes, setPendingDeletes] = useState<DeleteCard[]>([]);
  const pendingDelete = pendingDeletes[0] ?? null;

  // null = the shown card was decided, show the next; a card joins the queue.
  // The function form edits the whole queue: a chat that is left takes its
  // own cards out of it (useChat `releaseCards`).
  const setPendingDelete = useCallback((val: DeleteCard | null | ((list: DeleteCard[]) => DeleteCard[])) => {
    setPendingDeletes(list =>
      typeof val === 'function' ? val(list) : val === null ? list.slice(1) : [...list, val]);
  }, []);

  const fetchLastWorkspace = useCallback(async (userId: number) => {
    if (!API || !user?.sessionToken) return;
    // Two awaits (the API call and the host mapping) stand between asking for the
    // remembered workspace and offering it. `lastWorkspacePath` is what the UI
    // offers to reopen, so restoring it after the user closed the workspace hands
    // back the thing they just dismissed.
    const halaGecerli = workspaceRequest.observe();
    try {
      const res = await axios.get(`${API}/last-workspace/${userId}`, {
        headers: { 'X-Session-Token': user.sessionToken }
      });
      const stored = res.data?.path;
      if (stored) {
        const host = await hostWorkspacePath(stored);
        if (!halaGecerli()) return;
        if (host) setLastWorkspacePath(host);
      }
    } catch (err) { console.error("Last workspace hatası:", err); }
  }, [API, user, workspaceRequest]);

  // Every step of `selectWorkspace` awaits something (existence check,
  // directory read, host→backend mapping), so two selections overlap freely:
  // auto-restore starts one while the user picks another, and a slow first
  // mapping made the slower selection the LAST writer — the database left on A
  // while the UI showed B, with no error on either side. `closeWorkspace` is
  // the third overlapping actor, which is why ownership is a counter rather
  // than the requested path: a close has no path to compare against, and an
  // in-flight selection used to commit after it and silently reopen.
  const selectWorkspace = useCallback(async (path: string) => {
    const gecerliMi = workspaceRequest.claim();
    // Klasör hâlâ var mı? (silinmiş/taşınmış workspace'i otomatik yüklemeyi engelle)
    if (ipc) {
      const exists = await ipc.invoke('path-exists', path);
      if (!gecerliMi()) return;
      if (!exists) {
        showToast(cevir('workspace.missing'), 'warning');
        // Geçersiz yolu temizle ki bir daha otomatik yüklenmeye çalışılmasın
        setLastWorkspacePath(null);
        setWorkspacePath(null);
        // This branch used to return here, leaving the PREVIOUS workspace's tree
        // and content area on screen under no workspace at all: the preview
        // stayed mounted on a path nothing could reload, and a still-pending read
        // for that workspace was free to refill the editor afterwards. Failing to
        // open a replacement has to leave the same empty state as closing.
        contentRequest.invalidate();
        setRootFolderPath(null);
        setFileTree([]);
        setExpandedDirs(new Set());
        setDirContents({});
        setPreviewFile(null);
        setOpenedFilePath(null);
        setCode('');
        setOriginalCode('');
        return;
      }
    }
    // The content area belongs to the workspace being left: a preview or an
    // editor buffer carried into the new workspace names a file the new one
    // does not contain, and every later reload of it asks IPC for a path
    // outside the now-selected workspace. Invalidating the content request too
    // stops a read still in flight for the old workspace from refilling it.
    contentRequest.invalidate();
    setPreviewFile(null);
    setOpenedFilePath(null);
    setCode('');
    setOriginalCode('');
    setWorkspacePath(path);
    setRootFolderPath(path);
    if (ipc) {
      const entries = await ipc.invoke('read-directory', path, path);
      if (!gecerliMi()) return;
      setFileTree(entries || []);
    }
    setExpandedDirs(new Set());
    setDirContents({});
    if (user?.sessionToken && API) {
      const backendPath = await backendWorkspacePath(path);
      // A mapping that arrives after the user moved on describes a workspace
      // nobody is in: neither the warning nor the write belongs to it.
      if (!gecerliMi()) return;
      // null = Docker modu bu klasörü backend'e ADLANDIRAMIYOR (mount dışında).
      // Eski yolu göndermek, düzenleyicinin bir projede, ajanların başka bir
      // projede çalışması demekti — ikisi de başarılı görünerek.
      if (backendPath === null) {
        showToast(cevir('workspace.outsideDockerMount'), 'warning');
      } else {
        try {
          await axios.post(`${API}/save-workspace`,
            { user_id: user.id, path: backendPath },
            { headers: { 'X-Session-Token': user.sessionToken } }
          );
        } catch (err) { console.error("Workspace kaydetme hatası:", err); }
      }
    }
  }, [API, user, workspaceRequest, contentRequest]);

  const refreshFileTree = useCallback(async () => {
    if (!ipc || !workspacePath) return;
    // One read per expanded directory, so a refresh of a large tree stays in
    // flight long enough for a workspace switch to land inside it. Its entries
    // name files under the OLD root, so committing them shows the previous
    // project's files under the new project's name.
    const halaGecerli = workspaceRequest.observe();
    const entries = await ipc.invoke('read-directory', workspacePath, workspacePath);
    if (!halaGecerli()) return;
    setFileTree(entries || []);
    const newDirContents = { ...dirContents };
    for (const dir of expandedDirs) {
      try {
        const dirEntries = await ipc.invoke('read-directory', dir, workspacePath);
        newDirContents[dir] = dirEntries || [];
      } catch { }
    }
    if (!halaGecerli()) return;
    setDirContents(newDirContents);
    refreshGitStatus(workspacePath);
  }, [dirContents, expandedDirs, workspacePath, refreshGitStatus, workspaceRequest]);

  const openFolder = useCallback(async () => {
    if (!ipc) return;
    const halaGecerli = workspaceRequest.observe();
    const folderPath = await ipc.invoke('open-folder-dialog');
    // A dialog is dismissed by the OS, not by this hook, so its result can land
    // after an auto-restore or a close has already decided which workspace is
    // open. Watching rather than claiming: choosing a folder is only a decision
    // once the choice comes back.
    if (!halaGecerli()) return;
    if (folderPath) await selectWorkspace(folderPath);
  }, [selectWorkspace, workspaceRequest]);

  const openFilePicker = useCallback(async () => {
    if (!ipc) return;
    // Same ownership as `openFile`: this fills the shared content area, so it
    // claims that area up front. The dialog round trip is the slowest await in
    // the hook, and its result unconditionally clears `previewFile` — a model
    // opened while the dialog was up would vanish in favour of the older text
    // file the picker eventually returns.
    const gecerliMi = contentRequest.claim();
    const result = await ipc.invoke('open-file-dialog');
    if (!gecerliMi()) return;
    if (result) {
      setCode(result.content);
      setOriginalCode(result.content);
      setOpenedFilePath(result.path);
      setPreviewFile(null);
    }
  }, [contentRequest]);

  const closeWorkspace = useCallback(() => {
    // Closing is a decision ABOUT the workspace, so it has to outrank whatever
    // is still in flight for it: without this, a pending existence check or
    // directory read commits afterwards and reopens the workspace the user
    // just closed, with nothing on screen explaining why.
    workspaceRequest.invalidate();
    contentRequest.invalidate();
    setWorkspacePath(null);
    setRootFolderPath(null);
    setFileTree([]);
    setExpandedDirs(new Set());
    setDirContents({});
    setOpenedFilePath(null);
    setCode('');
    setPreviewFile(null);
  }, [workspaceRequest, contentRequest]);

  const openFile = useCallback(async (filePath: string) => {
    // The file tree routes by extension before it calls anything; this is the
    // OTHER way into the content area — chat file links and problem-list
    // entries call `openFile` directly. Without the same routing here a 3D
    // file goes to `read-file`, which answers `unsupported`, and the user gets
    // a refusal for a format the app can now display. Routing is a decision
    // about the path alone, so it precedes the channel check.
    if (routeForFile(filePath) !== 'text') { openPreview(filePath); return; }
    if (!ipc) return;
    // Claiming WITH the path: a path-changing operation that lands while this
    // read is out needs to know whether the read is for the path it changed.
    const gecerliMi = contentRequest.claim(filePath);
    const result = await ipc.invoke('read-file', filePath, workspacePath);
    // A slow read landing after the user picked something else would undo that
    // choice: it writes the editor and clears `previewFile`, so a model the
    // user selected meanwhile disappears in favour of the older text file. The
    // toasts below belong to the abandoned request too — a warning about a file
    // nobody is waiting for is noise attached to the wrong screen.
    if (!gecerliMi()) return;
    contentRequest.settle();
    if (result?.error === 'unsupported') {
      showToast(cevir('file.unsupported'), 'warning');
      return;
    }
    if (result?.error === 'too-large') {
      showToast(cevir('file.tooLarge'), 'warning');
      return;
    }
    if (result && result.content != null) {
      setCode(result.content);
      setOriginalCode(result.content);
      setOpenedFilePath(result.path);
      setPreviewFile(null);
      return;
    }
    // ⚠️ Bu dal eskiden SESSİZDİ ve bu bir arıza sınıfıydı: ana süreçteki
    // handler her hatada `null` dönüyor (dosya yok, workspace dışı, okuma
    // hatası, workspace hiç seçilmemiş — hepsi aynı `null`). Dosya ağacından
    // tıklandığında bu nadirdi, ama sohbetteki bir dosya LİNKİ artık buraya
    // düşebiliyor: model olmayan ya da workspace dışında bir yol yazabilir.
    // Sessiz dönmek, kullanıcıya tıkladığı şeyin bozuk olduğunu değil
    // uygulamanın bozuk olduğunu düşündürür.
    //
    // Sebebi ayırt edemiyoruz (handler tek bir `null` döndürüyor, bunu
    // değiştirmek onay/güvenlik yüzeyine dokunan ayrı bir iş), o yüzden mesaj
    // TAHMİN ETMİYOR — ne bilmediğimizi söylüyor ve yolu gösteriyor ki
    // kullanıcı kendisi karar verebilsin.
    showToast(cevir('file.openFailed', { yol: filePath }), 'warning');
  }, [workspacePath, showToast, openPreview, contentRequest]);

  const toggleDir = useCallback(async (dirPath: string) => {
    const next = new Set(expandedDirs);
    if (next.has(dirPath)) {
      next.delete(dirPath);
    } else {
      next.add(dirPath);
      if (!dirContents[dirPath]) {
        // `next` was derived from the expansion set of the workspace this call
        // started in, so committing it after a switch both caches a directory of
        // the old project and reopens a folder the new tree does not have.
        const halaGecerli = workspaceRequest.observe();
        const entries = await ipc.invoke('read-directory', dirPath, workspacePath);
        if (!halaGecerli()) return;
        setDirContents(prev => ({ ...prev, [dirPath]: entries || [] }));
      }
    }
    setExpandedDirs(next);
  }, [dirContents, expandedDirs, workspacePath, workspaceRequest]);

  const handleTreeDelete = useCallback(async (entry: FileEntry) => {
    setTreeContextMenu(null);
    // The confirmation dialog is open for as long as the user takes to answer,
    // so the workspace can change entirely while the question is on screen.
    const halaGecerli = pathOperationScope();
    if (!(await confirmDialog(cevir('file.deleteConfirm', { ad: entry.name })))) return;
    const res = await ipc.invoke('delete-entry', entry.path, workspacePath);
    // This entry point can also delete a FOLDER, so descendants go with it —
    // which `applyContentPathChange` handles as part of "the path is gone".
    if (res?.success && halaGecerli()) applyContentPathChange(entry.path, null);
    refreshFileTree();
  }, [refreshFileTree, workspacePath, applyContentPathChange, pathOperationScope]);

  const submitRename = useCallback(async () => {
    if (!renamingPath || !renameValue.trim()) { setRenamingPath(null); return; }
    const oldPath = renamingPath;
    const halaGecerli = pathOperationScope();
    const res = await ipc.invoke('rename-entry', oldPath, renameValue.trim(), workspacePath);
    setRenamingPath(null);
    if (!res?.success) return;
    // The main handler renames to `path.join(dirname(old), newName)` and returns
    // exactly that path, so the reported `newPath` is followable.
    const newPath: string | null = typeof res.newPath === 'string' ? res.newPath : null;
    // A rename that lands after the workspace changed describes a file in a
    // project the content area has left; following it would move the new
    // workspace's occupant onto an old workspace's path.
    if (halaGecerli()) applyContentPathChange(oldPath, newPath);
    refreshFileTree();
  }, [refreshFileTree, renameValue, renamingPath, workspacePath, applyContentPathChange, pathOperationScope]);

  const submitTreeCreate = useCallback(async () => {
    if (!treeCreating || !treeCreateValue.trim()) { setTreeCreating(null); return; }
    const { parentPath, type } = treeCreating;
    const newPath = `${parentPath}/${treeCreateValue.trim()}`;
    setTreeCreating(null);
    if (type === 'file') {
      await ipc.invoke('create-file', newPath, workspacePath);
    } else {
      await ipc.invoke('create-folder', newPath, workspacePath);
    }
    refreshFileTree();
  }, [refreshFileTree, treeCreateValue, treeCreating, workspacePath]);

  const suggestFilePath = useCallback((name: string) => {
    const ext = name.split('.').pop()?.toLowerCase();
    
    // Proje yapısını kontrol et: Script mi Scripts mi?
    let scriptDir = 'Scripts';
    if (dirContents['Assets/Script']) scriptDir = 'Script';
    else if (dirContents['Assets/Scripts']) scriptDir = 'Scripts';

    if (ext === 'cs') return `Assets/${scriptDir}/${name}`;
    if (ext === 'shader' || ext === 'compute' || ext === 'hlsl' || ext === 'cginc') return `Assets/Shaders/${name}`;
    if (ext === 'json' || ext === 'txt' || ext === 'md' || ext === 'xml') return `Assets/Resources/${name}`;
    return `Assets/${name}`;
  }, [dirContents]);

  const deleteFile = useCallback(async (relativePath: string) => {
    if (!ipc || !workspacePath) return;
    const halaGecerli = pathOperationScope();
    const res = await ipc.invoke('delete-file', relativePath, workspacePath);
    if (res.success) {
      showToast(cevir('file.deleted'), 'success');
      refreshFileTree();
      // Only the workspace this delete started in: after a workspace switch the
      // same relative path can name a different, still-present file.
      if (halaGecerli()) applyContentPathChange(relativePath, null);
    } else {
      showToast(cevir('file.deleteError', { hata: res.error }), 'error');
    }
  }, [ipc, workspacePath, showToast, refreshFileTree, applyContentPathChange, pathOperationScope]);

  const handleExportToUnity = useCallback(async (codeString: string) => {
    if (!workspacePath) return;
    const targetDir = `${workspacePath}/Assets/Scripts`;
    const classMatch = codeString.match(/class\s+(\w+)/);
    const suggestedName = `${classMatch ? classMatch[1] : 'NewScript'}.cs`;
    const allClasses = [...codeString.matchAll(/class\s+(\w+)/g)];

    if (allClasses.length > 1) {
      const files = splitCodeIntoFiles(codeString, workspacePath);
      setExportFileName(suggestedName);
      setExportModal({ isOpen: true, codeString, suggestedName, targetDir, existingFile: false, multiFile: true, files, exportResult: null });
    } else {
      const targetPath = `${targetDir}/${suggestedName}`;
      const exists = ipc ? await ipc.invoke('file-exists', targetPath, workspacePath) : false;
      setExportFileName(suggestedName);
      setExportModal({ isOpen: true, codeString, suggestedName, targetDir, existingFile: exists, multiFile: false, files: [{ name: suggestedName, code: codeString, path: targetPath }], exportResult: null });
    }
  }, [workspacePath]);

  const handleTreeDragStart = useCallback((e: React.DragEvent, entry: FileEntry) => {
    e.stopPropagation();
    setTreeDragSource(entry);
    e.dataTransfer.effectAllowed = 'copyMove';
    e.dataTransfer.setData('application/x-gamachine-file', JSON.stringify(entry));
    e.dataTransfer.setData('text/plain', entry.path);
  }, []);

  const handleTreeDragOver = useCallback((e: React.DragEvent, entry: FileEntry) => {
    if (!treeDragSource || !entry.isDirectory) return;
    if (entry.path === treeDragSource.path || entry.path.startsWith(treeDragSource.path + '/')) return;
    e.preventDefault();
    e.stopPropagation();
    setTreeDragTarget(entry.path);
  }, [treeDragSource]);

  const handleTreeDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setTreeDragTarget(null);
  }, []);

  const handleTreeDrop = useCallback(async (e: React.DragEvent, targetEntry: FileEntry) => {
    e.preventDefault();
    e.stopPropagation();
    setTreeDragTarget(null);
    if (!treeDragSource || !targetEntry.isDirectory) return;
    if (targetEntry.path === treeDragSource.path || targetEntry.path.startsWith(treeDragSource.path + '/')) return;
    const sourcePath = treeDragSource.path;
    const sourceParent = sourcePath.substring(0, sourcePath.lastIndexOf('/'));
    if (sourceParent === targetEntry.path) { setTreeDragSource(null); return; }
    // The drop is answered asynchronously, so a workspace switch can land in
    // between; the moved path then belongs to a workspace the content area has
    // already left.
    const halaGecerli = pathOperationScope();
    const res = await ipc?.invoke('move-entry', sourcePath, targetEntry.path, workspacePath);
    setTreeDragSource(null);
    if (!res?.success) return;
    // `move-entry` reports the destination the same way `rename-entry` does, so
    // the content area follows the file it was showing into its new directory.
    if (halaGecerli()) {
      applyContentPathChange(sourcePath, typeof res.newPath === 'string' ? res.newPath : null);
    }
    refreshFileTree();
  }, [refreshFileTree, treeDragSource, workspacePath, applyContentPathChange, pathOperationScope]);

  const handleTreeContextMenu = useCallback((e: React.MouseEvent, entry: FileEntry) => {
    e.preventDefault();
    e.stopPropagation();
    setTreeContextMenu({ x: e.clientX, y: e.clientY, entry });
  }, []);

  const startRename = useCallback((entry: FileEntry) => {
    setRenamingPath(entry.path);
    setRenameValue(entry.name);
    setTreeContextMenu(null);
  }, []);

  const startTreeCreate = useCallback((parentPath: string, type: 'file' | 'folder') => {
    setTreeContextMenu(null);
    setTreeCreating({ parentPath, type });
    setTreeCreateValue(type === 'file' ? 'NewScript.cs' : 'YeniKlasor');
    if (!expandedDirs.has(parentPath)) {
      setExpandedDirs(new Set([...expandedDirs, parentPath]));
    }
  }, [expandedDirs]);

  const changeExportDir = useCallback(async () => {
    if (!ipc || !exportModal) return;
    const newDir = await ipc.invoke('open-folder-dialog');
    if (newDir) setExportModal({ ...exportModal, targetDir: newDir });
  }, [exportModal]);

  const exportSingleFile = useCallback(async () => {
    if (!ipc || !workspacePath || !exportModal) return;
    const fileName = exportModal.suggestedName;
    const content = exportModal.codeString;
    const targetPath = `${exportModal.targetDir}/${fileName}`;
    const res = await ipc.invoke('write-file', targetPath, content, workspacePath);
    if (res?.success) {
      setExportModal({ ...exportModal, exportResult: { success: true, message: cevir('export.fileDone', { ad: fileName }) } });
      refreshFileTree();
      showToast(cevir('export.toUnity'), 'success');
    } else {
      setExportModal({ ...exportModal, exportResult: { success: false, message: cevir('export.writeFailed') } });
    }
  }, [exportModal, refreshFileTree, showToast, workspacePath]);

  const exportMultipleFiles = useCallback(async () => {
    if (!ipc || !workspacePath || !exportModal) return;
    let successCount = 0;
    for (const f of exportModal.files) {
      const targetPath = `${exportModal.targetDir}/${f.name}`;
      const res = await ipc.invoke('write-file', targetPath, f.code, workspacePath);
      if (res?.success) successCount++;
    }
    setExportModal({ ...exportModal, exportResult: { success: true, message: cevir('export.multiDone', { sayi: successCount }) } });
    refreshFileTree();
    showToast(cevir('export.multiToUnity', { sayi: successCount }), 'success');
  }, [exportModal, refreshFileTree, showToast, workspacePath]);

  return {
    workspacePath, lastWorkspacePath, fileTree, openedFilePath, code, setCode, setOpenedFilePath,
    isDirty, saveFile, previewFile, openPreview, closePreview,
    fetchLastWorkspace, selectWorkspace, openFolder, closeWorkspace, openFile, refreshFileTree,
    openFilePicker, treeCreating, setTreeCreating, treeCreateValue, setTreeCreateValue,
    submitTreeCreate, expandedDirs, dirContents, toggleDir, treeDragSource, treeDragTarget,
    renamingPath, renameValue, setRenameValue, submitRename, setRenamingPath,
    handleTreeDragStart, handleTreeDragOver, handleTreeDragLeave, handleTreeDrop,
    handleTreeContextMenu, startRename, startTreeCreate, handleTreeDelete,
    treeContextMenu, setTreeContextMenu, exportModal, setExportModal, exportFileName,
    setExportFileName, changeExportDir, exportSingleFile, exportMultipleFiles,
    suggestFilePath, pendingGenFiles, setPendingGenFiles,
    pendingDelete, setPendingDelete, deleteFile, handleExportToUnity,
    gitStatus, refreshGitStatus,
    rootFolderPath: workspacePath
  };
};
