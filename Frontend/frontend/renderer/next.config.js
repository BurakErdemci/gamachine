// REMOTE_WEB=1 builds the export the relay serves under /app/; any other build is the desktop one.
const remote = process.env.REMOTE_WEB === '1'

/** @type {import('next').NextConfig} */
module.exports = {
  output: 'export',
  distDir: remote ? '../app-remote' : process.env.NODE_ENV === 'production' ? '../app' : '.next',
  ...(remote && {
    basePath: '/app',
    assetPrefix: '/app',
    env: { NEXT_PUBLIC_BASE_PATH: '/app' },
  }),
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  webpack: (config, { webpack }) => {
    if (remote) {
      // fonts.css keeps root-absolute font URLs (the desktop serves them from /), which basePath
      // does not rewrite; under /app/ they would 404 on the relay.
      config.plugins.push({
        apply(compiler) {
          compiler.hooks.thisCompilation.tap('RemoteFontBase', (compilation) => {
            compilation.hooks.processAssets.tap(
              { name: 'RemoteFontBase', stage: webpack.Compilation.PROCESS_ASSETS_STAGE_OPTIMIZE },
              (assets) => {
                for (const [name, asset] of Object.entries(assets)) {
                  if (!name.endsWith('.css')) continue
                  const css = asset.source().toString()
                  if (!css.includes('url(/fonts/')) continue
                  compilation.updateAsset(name, new webpack.sources.RawSource(css.replaceAll('url(/fonts/', 'url(/app/fonts/')))
                }
              },
            )
          })
        },
      })
    }
    return config
  },
}
