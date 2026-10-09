// Minimal After Effects project model for tests/rebuild.test.js.

function CompItem(){} function AVLayer(){} function FolderItem(){} function FootageItem(){}
var items = [], nextId = 1;
function comp(name, w, h) { var c = new CompItem(); c.id = nextId++; c.name = name; c.comment = ""; c.width = w||3840; c.height = h||2160;
  c.pixelAspect = 1; c.duration = 5000; c.frameRate = 29.97; c.layers_ = [{},{},{}];
  Object.defineProperty(c, "numLayers", { get: function(){ return this.layers_.length; } });
  c.layer = function(i){ var self = this; return { remove: function(){ self.layers_.splice(i-1,1); } }; };
  items.push(c); return c; }
var app = { project: {
  activeItem: null,
  get numItems(){ return items.length; },
  item: function(i){ return items[i-1]; },
  items: { addComp: function(n,w,h){ return comp(n,w,h); }, addFolder: function(n){ var f = new FolderItem(); f.name = n; items.push(f); return f; } }
}};
var __vcPanelHost = true, $ = { global: {} };
