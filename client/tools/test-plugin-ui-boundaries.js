'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '../src/js/pages.js'), 'utf8');
const systemSettings = source.slice(source.indexOf('async function renderSettings(ctx)'), source.indexOf('async function renderExtensions(ctx)'));
const settingsHome = source.slice(source.indexOf('async function renderSettingsHome(ctx)'), source.indexOf('async function renderManagement(ctx)'));
const abilityGroup = source.slice(source.indexOf("{ id: 'ability'"), source.indexOf("{ id: 'system'"));

assert(!systemSettings.includes("['wechat', '微信桥接']"), 'WeChat Bridge must not be a fixed Core settings entry');
assert(!source.includes("k: 'desktop_extensions'"), 'Wallpaper must not have a fixed desktop_extensions route');
assert(!settingsHome.includes("title:'桌面扩展'"), 'settings home must not expose a dedicated desktop extensions tile');
assert(settingsHome.includes("title:'插件与扩展'"), 'settings home must retain the generic plugin manager entry');
assert(abilityGroup.includes("k: 'extensions'"), 'generic extension manager route must remain reachable');

console.log('PASS: Core settings expose only the generic plugin/extensions entry; no fixed Wallpaper or WeChat route remains.');
