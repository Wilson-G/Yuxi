import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MOBILE_LAYOUT_MAX_WIDTH,
  isMobileLayoutViewport
} from '../../src/utils/responsiveLayout.js'

test('移动端布局覆盖常用窄屏和 768px 边界', () => {
  assert.equal(isMobileLayoutViewport(375), true)
  assert.equal(isMobileLayoutViewport(390), true)
  assert.equal(isMobileLayoutViewport(MOBILE_LAYOUT_MAX_WIDTH), true)
  assert.equal(isMobileLayoutViewport(MOBILE_LAYOUT_MAX_WIDTH + 1), false)
})
