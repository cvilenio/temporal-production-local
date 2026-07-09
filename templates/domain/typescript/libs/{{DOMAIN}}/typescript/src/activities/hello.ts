import { ActivityName } from "../temporal-ids.js";

export async function sayHello(name: string): Promise<string> {
  return `Hello, ${name}!`;
}

export { ActivityName };
