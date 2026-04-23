var ZoteroPaperAgentPrefs = {
  init(win) {
    this.window = win;
    this.document = win.document;
    this.bindPathButton("zpa-choose-daemon", "zpa-daemon-path", "extensions.zotero-paper-agent.daemon_path", "file", "Select daemon.py");
    this.bindPathButton("zpa-choose-python", "zpa-python-path", "extensions.zotero-paper-agent.python_path", "file", "Select Python");
    this.bindPathButton("zpa-choose-marker", "zpa-marker-cli", "extensions.zotero-paper-agent.marker_cli", "file", "Select Marker CLI");
    this.bindPathButton("zpa-choose-input", "zpa-input-root", "extensions.zotero-paper-agent.input_root", "dir", "Select PDF Root");
    this.bindPathButton("zpa-choose-output", "zpa-output-root", "extensions.zotero-paper-agent.output_root", "dir", "Select Output Root");
    this.bindPathButton("zpa-choose-hf", "zpa-hf-home", "extensions.zotero-paper-agent.hf_home", "dir", "Select Hugging Face Cache");
  },

  bindPathButton(buttonId, inputId, prefKey, mode, title) {
    this.document.getElementById(buttonId)?.addEventListener("command", () => {
      this.browsePath(inputId, prefKey, mode, title);
    });
  },

  browsePath(inputId, prefKey, mode, title) {
    let fp = Components.classes["@mozilla.org/filepicker;1"]
      .createInstance(Components.interfaces.nsIFilePicker);
    fp.init(this.window, title, mode === "dir" ? fp.modeGetFolder : fp.modeOpen);
    fp.appendFilters(fp.filterAll);

    let currentVal = Zotero.Prefs.get(prefKey, true);
    if (currentVal) {
      try {
        let currentFile = Zotero.File.pathToFile(currentVal);
        fp.displayDirectory = currentFile.isDirectory() ? currentFile : currentFile.parent;
      } catch (e) {
        // Ignore invalid paths.
      }
    }

    fp.open((returnValue) => {
      if (returnValue !== fp.returnOK) {
        return;
      }
      let path = fp.file.path;
      this.document.getElementById(inputId).value = path;
      Zotero.Prefs.set(prefKey, path, true);
    });
  },
};

