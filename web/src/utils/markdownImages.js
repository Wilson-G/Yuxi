const AUTHENTICATED_IMAGE_PATH_RE =
  /\/api\/(?:knowledge\/databases\/[^/]+\/images|document-images)\//

/** 判断 Markdown 图片是否必须经当前登录用户鉴权读取。 */
export const isAuthenticatedMarkdownImageUrl = (url) =>
  AUTHENTICATED_IMAGE_PATH_RE.test(String(url || ''))
