/*
 * Minimal CSInterface — only the pieces the panel uses.
 *
 * Adobe ships a much larger CSInterface.js, but the panel needs four things:
 * run ExtendScript, find its own folder on disk, know the host app, and read
 * the host's UI colours so the panel matches After Effects' theme.
 */
/*global window*/
"use strict";

var SystemPath = {
    EXTENSION: "extension",
    USER_DATA: "userData",
    COMMON_FILES: "commonFiles",
    MY_DOCUMENTS: "myDocuments",
    HOST_APPLICATION: "hostApplication"
};

function CSInterface() {}

CSInterface.prototype.getOSInformation = function () {
    return window.navigator.platform.indexOf("Mac") >= 0 ? "Mac OS X" : "Windows";
};

CSInterface.prototype.evalScript = function (script, callback) {
    if (!window.__adobe_cep__) {
        if (callback) { callback("EvalScript error: not running inside CEP"); }
        return;
    }
    window.__adobe_cep__.evalScript(script, callback || function () {});
};

CSInterface.prototype.getSystemPath = function (type) {
    if (!window.__adobe_cep__) { return ""; }
    var path = decodeURI(window.__adobe_cep__.getSystemPath(type));
    return this.getOSInformation().indexOf("Mac") >= 0
        ? path.replace("file://", "")
        : path.replace("file:///", "");
};

CSInterface.prototype.getHostEnvironment = function () {
    if (!window.__adobe_cep__) { return { appName: "UNKNOWN", appVersion: "0" }; }
    return JSON.parse(window.__adobe_cep__.getHostEnvironment());
};

CSInterface.prototype.getHostCapabilities = function () {
    return JSON.parse(window.__adobe_cep__.getHostCapabilities());
};

CSInterface.prototype.addEventListener = function (type, listener, obj) {
    if (window.__adobe_cep__) { window.__adobe_cep__.addEventListener(type, listener, obj); }
};

CSInterface.prototype.closeExtension = function () {
    if (window.__adobe_cep__) { window.__adobe_cep__.closeExtension(); }
};

CSInterface.prototype.openURLInDefaultBrowser = function (url) {
    if (window.cep && window.cep.util) { window.cep.util.openURLInDefaultBrowser(url); }
};

/* Node access differs a little across CEP versions. */
function nodeRequire(name) {
    if (typeof window.cep_node !== "undefined" && window.cep_node.require) {
        return window.cep_node.require(name);
    }
    if (typeof window.require === "function") { return window.require(name); }
    throw new Error(
        "Node.js is not available in this panel. Check that --enable-nodejs is " +
        "in the manifest's CEFCommandLine."
    );
}
