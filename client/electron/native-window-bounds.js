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
  const width = finiteOr(bounds && bounds.width, null);
  const height = finiteOr(bounds && bounds.height, null);
  if ([contentX, contentY, offsetX, offsetY, width, height].some(value => value === null)) return null;
  if (width <= 1 || height <= 1) return null;

  // Electron Rectangle fields are signed integers. Round the outer edges so
  // fractional CSS layout survives DPI conversion without shrinking the slot.
  const left = Math.floor(contentX + offsetX);
  const top = Math.floor(contentY + offsetY);
  const right = Math.ceil(contentX + offsetX + width);
  const bottom = Math.ceil(contentY + offsetY + height);
  const dip = {
    x: left,
    y: top,
    width: right - left,
    height: bottom - top,
  };
  return [left, top, right, bottom, dip.width, dip.height].every(
    value => Number.isInteger(value) && value >= -2147483648 && value <= 2147483647,
  ) ? dip : null;
}

module.exports = { normalizeDipBounds };

