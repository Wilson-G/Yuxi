import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { getKbTypeLabel } from '../../src/utils/kb_utils.js'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const sourceRoot = path.join(webRoot, 'src')
const forbiddenLinks = [
  'github.com/xerrors/Yuxi',
  'xerrors.github.io/Yuxi',
  'img.shields.io/github/stars/xerrors/Yuxi'
]

const listSourceFiles = async (directory) => {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(
    entries.map((entry) => {
      const entryPath = path.join(directory, entry.name)
      return entry.isDirectory() ? listSourceFiles(entryPath) : [entryPath]
    })
  )
  return files.flat().filter((filePath) => /\.(js|vue)$/.test(filePath))
}

test('前端不再包含旧项目仓库、文档站和 Star 链接', async () => {
  const sourceFiles = await listSourceFiles(sourceRoot)

  for (const filePath of sourceFiles) {
    const source = await readFile(filePath, 'utf8')
    for (const forbiddenLink of forbiddenLinks) {
      assert.equal(
        source.includes(forbiddenLink),
        false,
        `${path.relative(webRoot, filePath)} 仍包含 ${forbiddenLink}`
      )
    }
  }
})

test('兼容 Milvus 知识库使用中性产品标签', () => {
  assert.equal(getKbTypeLabel('milvus'), '内置')
})
