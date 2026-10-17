import {defineConfig} from 'vite';
import vue from '@vitejs/plugin-vue';
import {fileURLToPath} from 'node:url';
export default defineConfig({root:fileURLToPath(new URL('./education',import.meta.url)),base:'./',plugins:[vue()],build:{outDir:fileURLToPath(new URL('./resources/education',import.meta.url)),emptyOutDir:true}});
