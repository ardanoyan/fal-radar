/** Static export: the whole site is plain files built from ../data at build time. */
const config = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
};

export default config;
