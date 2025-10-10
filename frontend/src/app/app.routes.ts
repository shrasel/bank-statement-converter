import { Routes } from '@angular/router';
import { HomeComponent } from './pages/home.component';
import { InspectComponent } from './pages/inspect.component';

export const routes: Routes = [
  {
    path: '',
    component: HomeComponent
  },
  {
    path: 'inspect',
    component: InspectComponent
  }
];
