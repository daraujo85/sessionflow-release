// Cria um usuário de aplicação dedicado ao DB `sessionflow`,
// escopado só a esse banco (não usa o root). Roda 1x na inicialização do volume.
const dbName = process.env.MONGO_INITDB_DATABASE || 'sessionflow';
const appUser = process.env.MONGO_APP_USERNAME;
const appPass = process.env.MONGO_APP_PASSWORD;

if (appUser && appPass) {
  const appDb = db.getSiblingDB(dbName);
  appDb.createUser({
    user: appUser,
    pwd: appPass,
    roles: [{ role: 'readWrite', db: dbName }],
  });
  print(`[sessionflow] usuário de aplicação '${appUser}' criado no DB '${dbName}'`);
} else {
  print('[sessionflow] MONGO_APP_USERNAME/PASSWORD ausentes — pulei criação do usuário de app');
}
