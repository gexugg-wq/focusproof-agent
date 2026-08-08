/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    cpus: 1,
    webpackBuildWorker: false
  },
  generateBuildId: async () => process.env.SOURCE_DATE_EPOCH ?? "1735689600"
};

export default nextConfig;
