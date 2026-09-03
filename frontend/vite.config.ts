import react from '@vitejs/plugin-react';
import { viteCommonjs } from '@originjs/vite-plugin-commonjs';
import { defineConfig, loadEnv, type ViteDevServer } from 'vite';
import path from 'node:path';
import { readdir, readFile } from 'node:fs/promises';
import type { IncomingMessage, ServerResponse } from 'node:http';
import crypto from 'node:crypto';

type Next = (err?: unknown) => void;

// SINGLETON: Track if middleware has been registered (prevents memory leak on restarts)
let middlewareRegistered = false;
let lastRestartTime = 0;
const RESTART_THROTTLE_MS = 2000; // Prevent restarts within 2 seconds

// Cache for login page HTML (loaded once from external file)
let loginPageHtmlCache: string | null = null;
// Cache for login page assets (SVG logo)
let loginLogoCache: Buffer | null = null;

// Load login page HTML from external file (eliminates 15KB closure capture per restart)
async function getLoginPageHtml(): Promise<string> {
  if (!loginPageHtmlCache) {
    const loginPagePath = path.resolve(__dirname, 'src/login.html');
    loginPageHtmlCache = await readFile(loginPagePath, 'utf-8');
  }
  return loginPageHtmlCache;
}

// Load login logo SVG for direct serving (bypasses Vite static for external hostname compatibility)
async function getLoginLogo(): Promise<Buffer> {
  if (!loginLogoCache) {
    const logoPath = path.resolve(__dirname, 'public/nils-icon.svg');
    loginLogoCache = await readFile(logoPath);
  }
  return loginLogoCache;
}

// Parse cookies from request
function parseCookies(req: IncomingMessage): Record<string, string> {
  const cookies: Record<string, string> = {};
  const cookieHeader = req.headers.cookie;
  if (cookieHeader) {
    cookieHeader.split(';').forEach((cookie) => {
      const [name, ...rest] = cookie.trim().split('=');
      if (name && rest.length > 0) {
        cookies[name] = decodeURIComponent(rest.join('='));
      }
    });
  }
  return cookies;
}

// Timing-safe token comparison
function verifyToken(provided: string, expected: string): boolean {
  if (!provided || !expected) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(expected));
  } catch {
    return false;
  }
}

// Token auth plugin - IDEMPOTENT middleware registration (CRITICAL: prevents memory leak)
function tokenAuthPlugin() {
  const accessToken = process.env.APP_ACCESS_TOKEN || '';

  return {
    name: 'token-auth',
    enforce: 'pre' as const,
    configureServer(server: ViteDevServer) {
      if (!accessToken) {
        console.log('[Auth] No APP_ACCESS_TOKEN set - auth disabled');
        return;
      }

      // CRITICAL FIX: Prevent duplicate middleware registration on config restarts
      // This is the primary cause of the memory leak - each restart was adding a new handler
      if (middlewareRegistered) {
        console.log('[Auth] Middleware already registered - skipping duplicate registration');
        return;
      }

      // RESTART THROTTLING: Prevent rapid restarts from phantom file changes
      const now = Date.now();
      if (now - lastRestartTime < RESTART_THROTTLE_MS) {
        console.log('[Auth] Restart throttled - too soon after last restart');
        return;
      }
      lastRestartTime = now;

      console.log('[Auth] Token protection enabled - registering middleware (ONCE)');
      middlewareRegistered = true;

      // Add middleware directly (runs after Vite internal but before HTML serving)
      server.middlewares.use(async (req: IncomingMessage, res: ServerResponse, next: Next) => {
        const url = req.url || '/';

        // Allow login page
        if (url === '/__login' || url.startsWith('/__login?')) {
          res.setHeader('Content-Type', 'text/html');
          const loginHtml = await getLoginPageHtml();
          res.end(loginHtml);
          return;
        }

        // Serve login logo directly (bypasses Vite static for external hostname compatibility)
        if (url === '/nils-icon.svg') {
          res.setHeader('Content-Type', 'image/svg+xml');
          res.setHeader('Cache-Control', 'public, max-age=86400');
          const logo = await getLoginLogo();
          res.end(logo);
          return;
        }

        // Allow Vite internal routes and static assets
        if (
          url.startsWith('/@') ||
          url.startsWith('/__vite') ||
          url.startsWith('/node_modules/') ||
          url.startsWith('/src/') ||
          url.match(/\.(js|ts|tsx|css|svg|png|jpg|ico|woff|woff2)(\?|$)/)
        ) {
          next();
          return;
        }

        // Check token from cookie or query param
        const cookies = parseCookies(req);
        const cookieToken = cookies['app_token'];
        const urlObj = new URL(url, 'http://localhost');
        const queryToken = urlObj.searchParams.get('token');

        // If token in URL, validate and set cookie
        if (queryToken) {
          if (verifyToken(queryToken, accessToken)) {
            res.setHeader('Set-Cookie', `app_token=${encodeURIComponent(queryToken)}; Path=/; SameSite=Strict; Max-Age=86400`);
            res.writeHead(302, { Location: urlObj.pathname });
            res.end();
            return;
          } else {
            res.writeHead(302, { Location: '/__login?error=1' });
            res.end();
            return;
          }
        }

        // Check cookie token
        if (cookieToken && verifyToken(cookieToken, accessToken)) {
          next();
          return;
        }

        // Redirect to login
        res.writeHead(302, { Location: '/__login' });
        res.end();
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const dataRootEnv = env.VITE_DATA_ROOT || '/data';
  const useRealFilesystem = env.VITE_USE_REAL_FILES === 'true';
  const dataRootResolved = path.resolve(dataRootEnv);

  return {
    resolve: {
      alias: {
        fs: path.resolve(__dirname, 'src/mocks/fs-mock.js'),
        path: path.resolve(__dirname, 'src/mocks/path-mock.js'),
      },
    },
    plugins: [tokenAuthPlugin(), react(), viteCommonjs()],
    // Cornerstone.js configuration
    optimizeDeps: {
      // Don't pre-bundle cornerstone packages - they have complex ESM/worker setups
      exclude: ['@cornerstonejs/dicom-image-loader'],
      include: ['dicom-parser'],
    },
    worker: {
      format: 'es' as const,
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      watch: {
        // CRITICAL FIX: Use polling mode for Docker stability
        // Native inotify has issues with Docker bind mounts causing phantom file changes
        // Polling is more reliable and with ~100 source files the overhead is minimal
        usePolling: true,
        interval: 1000,
        ignored: ['**/node_modules/**', '**/dist/**', '**/.git/**'],
      },
      fs: {
        allow: [process.cwd(), dataRootResolved],
      },
      proxy: {
        '/api': {
          target: process.env.VITE_API_URL || 'http://backend:8000',
          changeOrigin: true,
          timeout: 600_000,  // 10 min — body-part-qc apply can take minutes
        },
      },
    },
    build: {
      chunkSizeWarningLimit: 3000,
      minify: 'esbuild',
      esbuild: {
        drop: ['debugger'],
        pure: ['console.log', 'console.debug'],
      },
    },
    configureServer(server: ViteDevServer) {
      // /api/files handler for real filesystem mode
      if (!useRealFilesystem) return;

      server.middlewares.use('/api/files', async (req: IncomingMessage, res: ServerResponse, next: Next) => {
        if (req.method && req.method.toUpperCase() !== 'GET') {
          next();
          return;
        }

        try {
          const requestUrl = new URL(req.url ?? '', 'http://localhost');
          let requestedPath = requestUrl.searchParams.get('path') ?? dataRootEnv;
          if (!path.isAbsolute(requestedPath)) {
            requestedPath = path.join(dataRootEnv, requestedPath);
          }
          const normalized = path.resolve(requestedPath);
          if (!normalized.startsWith(dataRootResolved)) {
            res.statusCode = 400;
            res.end('Path outside data root');
            return;
          }

          const entries = await readdir(normalized, { withFileTypes: true });
          const payload = entries
            .filter((entry) => entry.isDirectory())
            .map((entry) => {
              const absoluteChild = path.join(normalized, entry.name);
              const displayPath = absoluteChild.replace(dataRootResolved, dataRootEnv).replace(/\\/g, '/');
              return {
                name: entry.name,
                path: displayPath.startsWith('/') ? displayPath : `/${displayPath}`,
                type: 'directory',
              };
            });

          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify(payload));
        } catch (error) {
          const code = (error as NodeJS.ErrnoException).code;
          if (code === 'ENOENT') {
            res.statusCode = 404;
            res.end('Not found');
          } else {
            res.statusCode = 500;
            res.end('Failed to list directories');
          }
        }
      });
    },
  };
});
