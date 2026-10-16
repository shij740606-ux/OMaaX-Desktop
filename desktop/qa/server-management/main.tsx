import React from 'react';
import {createRoot} from 'react-dom/client';
import ServerManagementView from '../../src/renderer/src/views/ServerManagementView';
import {SudoInstallProvider} from '../../src/renderer/src/components/SudoInstall';
import './qa.css';
import {installFixtures} from './fixtures';
installFixtures();
createRoot(document.getElementById('root')!).render(<SudoInstallProvider><ServerManagementView/></SudoInstallProvider>);
