import { Component } from '@angular/core';
import { Chat } from './chat/chat';

@Component({
  selector: 'app-root',
  imports: [Chat],
  template: '<app-chat />',
})
export class App {}
