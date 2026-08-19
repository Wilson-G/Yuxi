export const MOBILE_LAYOUT_MAX_WIDTH = 768

/** 判断当前视口是否应使用移动端布局。 */
export const isMobileLayoutViewport = (viewportWidth) =>
  viewportWidth <= MOBILE_LAYOUT_MAX_WIDTH
