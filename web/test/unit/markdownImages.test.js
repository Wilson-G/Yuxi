import assert from 'node:assert/strict'
import test from 'node:test'

import { isAuthenticatedMarkdownImageUrl } from '../../src/utils/markdownImages.js'

test('资料库和旧知识库解析图片都通过鉴权加载', () => {
  assert.equal(
    isAuthenticatedMarkdownImageUrl(
      '/api/document-images/material-libraries/lib/source-files/file/images/page.png'
    ),
    true
  )
  assert.equal(
    isAuthenticatedMarkdownImageUrl('/api/knowledge/databases/kb/images/folder/page.png'),
    true
  )
  assert.equal(isAuthenticatedMarkdownImageUrl('https://example.com/public.png'), false)
})
