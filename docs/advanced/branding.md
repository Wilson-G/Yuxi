# 品牌自定义

知铸默认使用“知铸 ZhiForge”名称、炉青主色与内置 SVG 标识。部署方可以通过信息配置覆盖组织名称、Logo、侧栏图标、登录背景、首页文案、版权和登录协议，无需修改业务代码。

品牌自定义只影响用户看到的内容。仓库、Python 包、`YUXI_*` 环境变量、API 路径、数据库标识和 `yuxi` CLI 命令继续使用现有技术名称。

## 创建本地品牌配置

复制默认模板：

```bash
cp backend/package/yuxi/config/static/info.template.yaml \
  backend/package/yuxi/config/static/info.local.yaml
```

编辑 `info.local.yaml`：

```yaml
organization:
  name: "示例组织"
  logo: "/brand/example-logo.svg"
  avatar: "/brand/example-mark.svg"
  login_bg: "/brand/example-login.jpg"

branding:
  name: "Example"
  title: "把团队知识，铸成专家的大脑"
  subtitle: "创建能理解方法、做出判断、解释依据的专家 Agent"

footer:
  copyright: "© 示例组织 2026"
  user_agreement_url: "/protocols/user-agreement.template.html"
  privacy_policy_url: "/protocols/privacy-policy.template.html"
```

| 字段 | 展示位置 |
| --- | --- |
| `organization.name` | 公开首页、登录页、应用侧栏和 CLI 授权页的主要名称 |
| `organization.logo` | 公开首页、登录页和 CLI 授权页的品牌图形 |
| `organization.avatar` | 应用侧栏展开与收起状态的紧凑图形 |
| `organization.login_bg` | 登录页和公开首页的背景图 |
| `branding.name` | 与组织名称并列的英文名或产品名 |
| `branding.title` | 公开首页的主要价值说明 |
| `branding.subtitle` | 公开首页的补充说明 |
| `footer.*` | 页脚版权及登录协议链接 |

Logo 和 avatar 应使用能在浅色、深色背景上清楚显示的 SVG 或透明 PNG。紧凑 avatar 需要在 28px 到 34px 下仍可辨认，不要依赖细小文字。登录背景应能安全裁切，页面会添加固定遮罩保证文字对比度。

## 指定配置文件

默认读取 `backend/package/yuxi/config/static/info.local.yaml`；也可以在 `.env` 中指定其他路径：

```env
YUXI_BRAND_FILE_PATH=backend/package/yuxi/config/static/info.local.yaml
```

如果指定文件不存在，系统使用 `info.template.yaml`。信息接口每次请求都会读取当前文件；修改配置后刷新页面即可获取新值。

## 登录协议

只有 `user_agreement_url` 和 `privacy_policy_url` 都有值时，登录页才显示协议勾选项。未勾选时，登录或首次初始化会提示用户先同意协议。

仓库提供两个 HTML 模板：

- `web/public/protocols/user-agreement.template.html`
- `web/public/protocols/privacy-policy.template.html`

可以编辑模板中的 `{{ORG_NAME}}`、`{{PRODUCT_NAME}}`、`{{EFFECTIVE_DATE}}` 等占位内容，也可以把 URL 指向部署方自己的协议页面。

## 修改主题颜色

组织信息配置不承载主题状态。改变全站配色时，修改现有全局 token：

- `web/src/assets/css/base.css`：浅色模式
- `web/src/assets/css/base.dark.css`：深色模式
- `web/src/stores/theme.js`：Ant Design Vue 的 `colorPrimary`

默认品牌色示例：

```css
:root {
  --main-color: #216b58;
  --main-bright: #2f8069;
  --brand-accent: #b94f3b;
  --brand-accent-soft: #faece8;
}
```

业务组件应继续使用 `--main-*`、`--gray-*` 和语义色，不在单个组件内复制一套新色板。开发环境会通过热重载反映 CSS 修改；生产部署需要重新构建前端镜像。
