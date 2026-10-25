'use strict';

function finiteOr(value, fallback) {
  if (value == null) return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeDipBounds(bounds, content) {
  const contentX = finiteOr(content && content.x, 0);
  const contentY = finiteOr(content && content.y, 0);
  const offsetX = finiteOr(bounds && bounds.x, 0);
  const offsetY = finiteOr(bounds && bounds.y, 0);
  const width = finiteOr(bounds && bounds.width, 1);
  const height = finiteOr(bounds && bounds.height, 1);
  if ([contentX, contentY, offsetX, offsetY, width, height].some(value => value === null)) return null;

  const dip = {
    x: contentX + offsetX,
    y: contentY + offsetY,
    width: Math.max(1, width),
    height: Math.max(1, height),
  };
  return Object.values(dip).every(Number.isFinite) ? dip : null;
}

module.exports = { normalizeDipBounds };

