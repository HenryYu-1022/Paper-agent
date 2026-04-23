PaperAgent = {
  id: null,
  version: null,
  rootURI: null,
  notifierID: null,
  addedElementIDs: [],
  pendingTrashPaths: new Map(),
  proc: null,
  subprocess: null,
  pendingRequests: new Map(),
  stdoutBuffer: "",
  nextRequestID: 1,
  runtimeConfigPath: null,

  init({ id, version, rootURI }) {
    this.id = id;
    this.version = version;
    this.rootURI = rootURI;
  },

  log(message) {
    Zotero.debug("Zotero Paper Agent: " + message);
  },

  getPref(key) {
    return Zotero.Prefs.get("extensions.zotero-paper-agent." + key, true);
  },

  setPref(key, value) {
    Zotero.Prefs.set("extensions.zotero-paper-agent." + key, value, true);
  },

  getMarkdownRoot() {
    let outputRoot = String(this.getPref("output_root") || "").replace(/[\\/]+$/, "");
    return outputRoot ? outputRoot + "/markdown" : "";
  },

  getInputRoot() {
    return String(this.getPref("input_root") || "").replace(/[\\/]+$/, "");
  },

  registerNotifier() {
    if (this.notifierID) {
      return;
    }
    this.notifierID = Zotero.Notifier.registerObserver(
      {
        notify: async (event, type, ids, extraData) => {
          if (type !== "item") {
            return;
          }
          await this.handleItemEvent(event, ids, extraData || {});
        },
      },
      ["item"],
      "zotero-paper-agent",
    );
  },

  unregisterNotifier() {
    if (!this.notifierID) {
      return;
    }
    Zotero.Notifier.unregisterObserver(this.notifierID);
    this.notifierID = null;
  },

  async handleItemEvent(event, ids, extraData) {
    if (event === "trash") {
      await this.rememberTrashPaths(ids);
      if (this.getPref("archive_on_trash")) {
        await this.archiveOrDeleteRemembered(ids, "archive_orphan");
      }
      return;
    }

    if (event === "delete") {
      await this.archiveOrDeleteRemembered(ids, "delete_orphan");
      return;
    }

    if ((event === "add" || event === "modify") && this.getPref("auto_convert")) {
      await Zotero.Promise.delay(event === "add" ? 1500 : 300);
      let attachments = await this.getPdfAttachmentsForIDs(ids);
      for (let attachment of attachments) {
        await this.processAttachment(attachment, { force: false });
      }
    }
  },

  async rememberTrashPaths(ids) {
    for (let id of ids) {
      try {
        let item = await Zotero.Items.getAsync(id);
        if (item && this.isPdfAttachment(item)) {
          let path = await item.getFilePathAsync();
          if (path) {
            this.pendingTrashPaths.set(id, path);
          }
        }
      } catch (e) {
        this.log("Could not remember trash path for " + id + ": " + e);
      }
    }
  },

  async archiveOrDeleteRemembered(ids, command) {
    for (let id of ids) {
      let path = this.pendingTrashPaths.get(id);
      if (!path) {
        continue;
      }
      try {
        await this.sendDaemonCommand({ command, path });
      } catch (e) {
        this.log(command + " failed for " + path + ": " + e);
      }
      if (command === "delete_orphan") {
        this.pendingTrashPaths.delete(id);
      }
    }
  },

  async getPdfAttachmentsForIDs(ids) {
    let attachments = [];
    for (let id of ids) {
      let item = await Zotero.Items.getAsync(id);
      if (!item) {
        continue;
      }
      if (this.isPdfAttachment(item)) {
        attachments.push(item);
        continue;
      }
      if (item.isRegularItem && item.isRegularItem()) {
        for (let attachmentID of item.getAttachments()) {
          let attachment = Zotero.Items.get(attachmentID);
          if (attachment && this.isPdfAttachment(attachment)) {
            attachments.push(attachment);
          }
        }
      }
    }
    return attachments;
  },

  isPdfAttachment(item) {
    return (
      item
      && item.isAttachment
      && item.isAttachment()
      && item.attachmentContentType === "application/pdf"
    );
  },

  async processAttachment(attachment, options = {}) {
    let item = attachment;
    if (this.getPref("auto_rename")) {
      item = await this.renameAttachmentFromMetadata(attachment);
    }
    let path = await item.getFilePathAsync();
    if (!path) {
      throw new Error("PDF attachment has no local file path");
    }
    return this.sendDaemonCommand({
      command: "convert",
      path,
      force: !!options.force,
    });
  },

  async renameAttachmentFromMetadata(attItem, retry = 0) {
    if (!this.isPdfAttachment(attItem)) {
      return attItem;
    }
    let filePath = await attItem.getFilePathAsync();
    if (!filePath || !attItem.parentItemID) {
      return attItem;
    }
    let parentItem = await Zotero.Items.getAsync(attItem.parentItemID);
    if (!parentItem) {
      return attItem;
    }

    let newName = Zotero.Attachments.getFileBaseNameFromItem(parentItem, {
      attachmentTitle: attItem.getField("title"),
    });
    let originalName = this.pathBasename(filePath);
    let extension = originalName.match(/\.[^.]+$/);
    if (extension) {
      newName += extension[0];
    }

    let renamed = await attItem.renameAttachmentFile(newName, false, true);
    if (renamed !== true && retry < 5) {
      await Zotero.Promise.delay(3000);
      return this.renameAttachmentFromMetadata(attItem, retry + 1);
    }

    await attItem.saveTx();
    return attItem;
  },

  async writeRuntimeConfig() {
    let daemonPath = String(this.getPref("daemon_path") || "");
    let config = {
      input_root: String(this.getPref("input_root") || ""),
      output_root: String(this.getPref("output_root") || ""),
      marker_cli: String(this.getPref("marker_cli") || "marker_single"),
      hf_home: String(this.getPref("hf_home") || ""),
      torch_device: String(this.getPref("torch_device") || "mps"),
      output_format: "markdown",
      force_ocr: false,
      disable_image_extraction: false,
      disable_multiprocessing: false,
      paginate_output: false,
      compute_sha256: true,
      daemon_idle_timeout_seconds: Number(this.getPref("idle_timeout") || 300),
      log_level: "INFO",
    };

    if (!config.input_root || !config.output_root || !config.hf_home || !daemonPath) {
      throw new Error("Zotero Paper Agent preferences are incomplete");
    }

    let profileDir = Services.dirsvc.get("ProfD", Components.interfaces.nsIFile).path;
    let configPath = PathUtils.join(profileDir, "zotero-paper-agent-settings.json");
    await IOUtils.writeUTF8(configPath, JSON.stringify(config, null, 2));
    this.runtimeConfigPath = configPath;
    return configPath;
  },

  getSubprocess() {
    if (this.subprocess) {
      return this.subprocess;
    }
    try {
      this.subprocess = ChromeUtils.importESModule("resource://gre/modules/Subprocess.sys.mjs").Subprocess;
    } catch (e) {
      this.subprocess = ChromeUtils.import("resource://gre/modules/Subprocess.jsm").Subprocess;
    }
    return this.subprocess;
  },

  async ensureDaemon() {
    if (this.proc) {
      return this.proc;
    }

    let daemonPath = String(this.getPref("daemon_path") || "");
    if (!daemonPath) {
      throw new Error("daemon.py path is not configured");
    }
    let pythonPath = String(this.getPref("python_path") || "python3");
    let configPath = await this.writeRuntimeConfig();
    let idleTimeout = String(Number(this.getPref("idle_timeout") || 300));
    let Subprocess = this.getSubprocess();

    this.proc = await Subprocess.call({
      command: pythonPath,
      arguments: [daemonPath, "--config", configPath, "--idle-timeout", idleTimeout],
    });
    this.readStdoutLoop(this.proc);
    this.readStderrLoop(this.proc);
    this.proc.wait().then(({ exitCode }) => {
      this.log("daemon exited: " + exitCode);
      this.rejectPendingRequests("daemon exited: " + exitCode);
      this.proc = null;
    });
    return this.proc;
  },

  async sendDaemonCommand(payload) {
    let proc = await this.ensureDaemon();
    let id = "zpa-" + this.nextRequestID++;
    let request = { id, ...payload };
    let promise = new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve, reject });
      Zotero.Promise.delay(10 * 60 * 1000).then(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error("daemon request timed out"));
        }
      });
    });
    await proc.stdin.write(JSON.stringify(request) + "\n");
    return promise;
  },

  async readStdoutLoop(proc) {
    try {
      let chunk;
      while ((chunk = await proc.stdout.readString())) {
        this.stdoutBuffer += chunk;
        let lines = this.stdoutBuffer.split(/\r?\n/);
        this.stdoutBuffer = lines.pop() || "";
        for (let line of lines) {
          if (line.trim()) {
            this.handleDaemonLine(line);
          }
        }
      }
    } catch (e) {
      this.log("stdout reader failed: " + e);
    }
  },

  async readStderrLoop(proc) {
    try {
      let chunk;
      while ((chunk = await proc.stderr.readString())) {
        this.log("daemon stderr: " + chunk.trim());
      }
    } catch (e) {
      this.log("stderr reader failed: " + e);
    }
  },

  handleDaemonLine(line) {
    let response;
    try {
      response = JSON.parse(line);
    } catch (e) {
      this.log("daemon produced non-JSON output: " + line);
      return;
    }
    let pending = this.pendingRequests.get(response.id);
    if (!pending) {
      return;
    }
    this.pendingRequests.delete(response.id);
    if (response.ok) {
      pending.resolve(response.result);
    } else {
      pending.reject(new Error(response.error || "daemon command failed"));
    }
  },

  rejectPendingRequests(message) {
    for (let [_id, pending] of this.pendingRequests) {
      pending.reject(new Error(message));
    }
    this.pendingRequests.clear();
  },

  async stopDaemon() {
    if (!this.proc) {
      return;
    }
    try {
      await this.sendDaemonCommand({ command: "shutdown" });
    } catch (e) {
      this.log("daemon shutdown request failed: " + e);
    }
    try {
      await this.proc.stdin.close();
    } catch (e) {
      // Ignore close races.
    }
    this.proc = null;
  },

  async getSelectedPdfAttachment() {
    let items = Zotero.getActiveZoteroPane().getSelectedItems();
    if (!items || !items.length) {
      return null;
    }
    let attachments = await this.getPdfAttachmentsForIDs(items.map((item) => item.id));
    return attachments[0] || null;
  },

  async convertSelected() {
    let attachment = await this.getSelectedPdfAttachment();
    if (!attachment) {
      this.showAlert("No PDF attachment found.");
      return;
    }
    try {
      await this.processAttachment(attachment, { force: false });
      this.showAlert("Conversion queued.");
    } catch (e) {
      this.showAlert("Conversion failed: " + e.message);
    }
  },

  async renameSelected() {
    let attachment = await this.getSelectedPdfAttachment();
    if (!attachment) {
      this.showAlert("No PDF attachment found.");
      return;
    }
    try {
      await this.renameAttachmentFromMetadata(attachment);
    } catch (e) {
      this.showAlert("Rename failed: " + e.message);
    }
  },

  async cleanupOrphans() {
    try {
      let result = await this.sendDaemonCommand({ command: "cleanup_orphans", mode: "archive" });
      this.showAlert("Archived " + result.cleaned + " orphan Markdown bundle(s).");
    } catch (e) {
      this.showAlert("Cleanup failed: " + e.message);
    }
  },

  async revealMarkdown() {
    let mdPath = await this.resolveSelectedMarkdownPath();
    if (!mdPath) {
      this.showAlert("Markdown file not found.");
      return;
    }
    Zotero.File.pathToFile(mdPath).reveal();
  },

  async openMarkdown() {
    let mdPath = await this.resolveSelectedMarkdownPath();
    if (!mdPath) {
      this.showAlert("Markdown file not found.");
      return;
    }
    if (this.dirExists(mdPath)) {
      mdPath = this.findMarkdownInBundle(mdPath) || mdPath;
    }
    Zotero.File.pathToFile(mdPath).launch();
  },

  async resolveSelectedMarkdownPath() {
    let attachment = await this.getSelectedPdfAttachment();
    if (!attachment) {
      return null;
    }
    let pdfPath = await attachment.getFilePathAsync();
    return pdfPath ? this.resolveMarkdownPath(pdfPath) : null;
  },

  resolveMarkdownPath(pdfPath) {
    let markdownRoot = this.getMarkdownRoot();
    if (!markdownRoot) {
      return null;
    }
    let inputRoot = this.getInputRoot();
    let normalizedPdf = this.normalizePath(pdfPath);
    let normalizedInput = this.normalizePath(inputRoot);
    let stem = this.pathStem(normalizedPdf);

    if (normalizedInput && normalizedPdf.startsWith(normalizedInput + "/")) {
      let relPath = normalizedPdf.slice(normalizedInput.length + 1);
      let relDir = this.pathDirname(relPath);
      let bundle = relDir ? markdownRoot + "/" + relDir + "/" + stem : markdownRoot + "/" + stem;
      let mdFile = bundle + "/" + stem + ".md";
      if (this.fileExists(mdFile)) {
        return mdFile;
      }
      if (this.dirExists(bundle)) {
        return bundle;
      }
    }

    let flatBundle = markdownRoot + "/" + stem;
    let flatFile = flatBundle + "/" + stem + ".md";
    if (this.fileExists(flatFile)) {
      return flatFile;
    }
    if (this.dirExists(flatBundle)) {
      return flatBundle;
    }
    return this.findBundleRecursive(markdownRoot, stem);
  },

  findMarkdownInBundle(bundlePath) {
    let stem = this.pathBasename(bundlePath);
    let direct = bundlePath + "/" + stem + ".md";
    if (this.fileExists(direct)) {
      return direct;
    }
    try {
      let dir = Zotero.File.pathToFile(bundlePath);
      let entries = dir.directoryEntries;
      while (entries.hasMoreElements()) {
        let entry = entries.getNext().QueryInterface(Components.interfaces.nsIFile);
        if (!entry.isDirectory() && entry.leafName.endsWith(".md")) {
          return entry.path;
        }
      }
    } catch (e) {
      this.log("bundle scan failed: " + e);
    }
    return null;
  },

  findBundleRecursive(dir, stem) {
    try {
      let dirObj = Zotero.File.pathToFile(dir);
      if (!dirObj.exists() || !dirObj.isDirectory()) {
        return null;
      }
      let entries = dirObj.directoryEntries;
      while (entries.hasMoreElements()) {
        let entry = entries.getNext().QueryInterface(Components.interfaces.nsIFile);
        if (!entry.isDirectory()) {
          continue;
        }
        if (entry.leafName === stem) {
          let mdFile = entry.path + "/" + stem + ".md";
          return this.fileExists(mdFile) ? mdFile : entry.path;
        }
        let found = this.findBundleRecursive(entry.path, stem);
        if (found) {
          return found;
        }
      }
    } catch (e) {
      this.log("recursive lookup failed: " + e);
    }
    return null;
  },

  normalizePath(path) {
    return String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
  },

  pathBasename(path) {
    let parts = this.normalizePath(path).split("/");
    return parts[parts.length - 1] || "";
  },

  pathStem(path) {
    let name = this.pathBasename(path);
    let dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(0, dot) : name;
  },

  pathDirname(path) {
    let parts = this.normalizePath(path).split("/");
    parts.pop();
    return parts.join("/");
  },

  fileExists(path) {
    try {
      let file = Zotero.File.pathToFile(path);
      return file.exists() && !file.isDirectory();
    } catch (e) {
      return false;
    }
  },

  dirExists(path) {
    try {
      let file = Zotero.File.pathToFile(path);
      return file.exists() && file.isDirectory();
    } catch (e) {
      return false;
    }
  },

  showAlert(message) {
    Services.prompt.alert(null, "Zotero Paper Agent", message);
  },

  addToWindow(window) {
    let doc = window.document;
    window.MozXULElement.insertFTLIfNeeded("zotero-paper-agent.ftl");
    let menu = doc.getElementById("zotero-itemmenu");
    if (!menu || doc.getElementById("zotero-paper-agent-menu")) {
      return;
    }

    let root = doc.createXULElement("menu");
    root.id = "zotero-paper-agent-menu";
    root.setAttribute("label", "Paper Agent");
    let popup = doc.createXULElement("menupopup");
    root.appendChild(popup);

    this.addMenuItem(doc, popup, "zotero-paper-agent-open-menuitem", "zotero-paper-agent-open", () => this.openMarkdown());
    this.addMenuItem(doc, popup, "zotero-paper-agent-reveal-menuitem", "zotero-paper-agent-reveal", () => this.revealMarkdown());
    popup.appendChild(doc.createXULElement("menuseparator"));
    this.addMenuItem(doc, popup, "zotero-paper-agent-convert-menuitem", "zotero-paper-agent-convert", () => this.convertSelected());
    this.addMenuItem(doc, popup, "zotero-paper-agent-rename-menuitem", "zotero-paper-agent-rename", () => this.renameSelected());
    popup.appendChild(doc.createXULElement("menuseparator"));
    this.addMenuItem(doc, popup, "zotero-paper-agent-cleanup-menuitem", "zotero-paper-agent-cleanup", () => this.cleanupOrphans());

    menu.appendChild(root);
    this.storeAddedElement(root);
  },

  addMenuItem(doc, popup, id, l10nID, command) {
    let item = doc.createXULElement("menuitem");
    item.id = id;
    item.setAttribute("data-l10n-id", l10nID);
    item.addEventListener("command", command);
    popup.appendChild(item);
  },

  addToAllWindows() {
    for (let win of Zotero.getMainWindows()) {
      if (win.ZoteroPane) {
        this.addToWindow(win);
      }
    }
  },

  storeAddedElement(elem) {
    this.addedElementIDs.push(elem.id);
  },

  removeFromWindow(window) {
    let doc = window.document;
    for (let id of this.addedElementIDs) {
      doc.getElementById(id)?.remove();
    }
    doc.querySelector('[href="zotero-paper-agent.ftl"]')?.remove();
  },

  removeFromAllWindows() {
    for (let win of Zotero.getMainWindows()) {
      if (win.ZoteroPane) {
        this.removeFromWindow(win);
      }
    }
  },

  async shutdown() {
    this.unregisterNotifier();
    this.removeFromAllWindows();
    await this.stopDaemon();
  },
};
