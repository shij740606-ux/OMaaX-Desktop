import {defineConfig, mergeConfig} from 'vite';
import {resolve} from 'node:path';
import base from '../../vite.tauri.config';
export default mergeConfig(base,defineConfig({cacheDir:resolve(__dirname,'../../../validation-local/navigation-vite-cache'),server:{host:'127.0.0.1',port:41742,strictPort:true}}));
