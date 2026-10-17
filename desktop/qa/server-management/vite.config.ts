import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import {resolve} from 'node:path';
export default defineConfig({root:__dirname,cacheDir:resolve(__dirname,'../../../validation-local/server-qa-vite-cache'),plugins:[react(),tailwindcss()],resolve:{dedupe:['react','react-dom'],alias:[{find:'../store',replacement:resolve(__dirname,'store.ts')},{find:'@shared',replacement:resolve(__dirname,'../../src/shared')}]},server:{host:'127.0.0.1',port:41739,strictPort:true,fs:{allow:[resolve(__dirname,'../..')]}}});
