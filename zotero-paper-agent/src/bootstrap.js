var PaperAgent;

function log(msg) {
  Zotero.debug("Zotero Paper Agent: " + msg);
}

function install() {
  log("Installed");
}

async function startup({ id, version, rootURI }) {
  log("Starting");
  Zotero.PreferencePanes.register({
    pluginID: "zotero-paper-agent@henryyu.github.io",
    src: rootURI + "preferences.xhtml",
    scripts: [rootURI + "preferences.js"],
  });

  Services.scriptloader.loadSubScript(rootURI + "paper-agent.js");
  PaperAgent.init({ id, version, rootURI });
  PaperAgent.addToAllWindows();
  PaperAgent.registerNotifier();
}

function onMainWindowLoad({ window }) {
  PaperAgent?.addToWindow(window);
}

function onMainWindowUnload({ window }) {
  PaperAgent?.removeFromWindow(window);
}

async function shutdown() {
  log("Shutting down");
  if (PaperAgent) {
    await PaperAgent.shutdown();
    PaperAgent = undefined;
  }
}

function uninstall() {
  log("Uninstalled");
}

