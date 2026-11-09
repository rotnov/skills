-- GENERATED. Do not edit. Quorum server baseline, adoption by an existing server.
-- Covers references/server-bootstrap.sql and supabase/migrations through 20261108140000.
-- Sources sha256 32c66895f79f44b19c12ba01cec091b445016bf0c5f938d44843984cdbb1512a (tests/quorum_baseline_support.py --check).
-- Regenerate: QUORUM_BASELINE_DOCKER=1 uv run --frozen python tests/quorum_baseline_support.py --write
-- Existing server only, in its own maintenance window, in one transaction. Changes no Quorum object; only records the baseline after the catalog matches.
-- See references/server.md#fresh-project and #baseline-maintenance-window.
SELECT pg_catalog.set_config('search_path', '', true);
DO $adopt$
DECLARE
 knowledge boolean := pg_catalog.to_regclass('quorum_private.knowledge_spaces') IS NOT NULL;
 problems text[];
BEGIN
 IF pg_catalog.to_regclass('quorum_private.schema_baseline') IS NOT NULL THEN
  RAISE EXCEPTION 'quorum_baseline_present';
 END IF;
 IF pg_catalog.current_setting('search_path') NOT IN ('','""') THEN
  RAISE EXCEPTION 'quorum_baseline_search_path';
 END IF;
 WITH expected(part,kind,identity,definition,prosrc) AS (VALUES
('core','extension','pgcrypto','20ef7739e8423f114899b767909a220c5dc4169d73a38196d7a2dbfac5166c4e',NULL),
('core','function','public.agent_join(uuid, uuid, text, text, text, text, text, boolean, uuid)','e32b024e12a2aa979cd117657d898b957539e633d62630040f9b0653440b090f','bb26047e016fbe074338e37150a10077181c700d6b67fff4eeb7a2ecb3b9fbf4'),
('core','function','public.agent_leave(uuid, text)','297ef6d76b2c620ae47355d312fb78e5c9d4fce4351fd130d433c927255a4d9d','bb86d4237828c493a2f303a9ec5d9bb11187ad02ca22d43cb2492fe33b294180'),
('core','function','public.agent_retire(uuid, uuid, text)','57fbbb58276202549890db5e51b28df322bf439224c0413419075ddde2bd110a','cfb3850a19ad962fe99243e6ab03fed85839f02811da4c1891b31b80de743d29'),
('core','function','public.agent_status(uuid)','409e56a9ddf9352cf7a93506d4a7ed9e141c90c4330b5b7b70330083e02a1cd3','f491d50767719a44591aff86a56fec0076bf04b3f357ad0a799feb3e38a4c52a'),
('core','function','public.attempt_update(uuid, bigint, text, text)','5fa997ba1d0b95f2d80f6b4bba41b49b90fc496c7cb77f33ac35ddd2ffcd329d','c2dfa6f6cf124ecd126e9fa5329e883a0fb0ae91b4e30c7eac5c901b4871ff9b'),
('core','function','public.context_read(uuid, text, bigint)','5a30d4ee20a0e5b117d4f7e7b6f9d754212795b8081a7ec87a14015194670e3f','27dd97b9c6f475882c1f44db802171ca096e7ef2c4f316283fd4f5bf4a8cc458'),
('core','function','public.conversation_read(uuid, uuid, uuid, bigint)','cf0afc61ad89e33104494ad958ec90331b16ab387dcfb04c87a71e260ec6f911','c75e39ea706f2467412d9958b4e6bd99db6a00ba5e2ed1b7d735142f963f39b6'),
('core','function','public.delivery_complete(uuid, bigint, text, text)','00504979d5bc4454eeb7c722cd5b75d200540b50e3bfd0f742053104af1cfbae','4d4eedbfb2a057926e7519b7fe7eba0df4eb23b338076c0759af340a09679220'),
('core','function','public.device_register(text, text)','74e4e68a889d30b32fe87a36f578efa8504fd11fd50b0d7fb4974fd0ae6a22d4','96254cac56486d32ddc091bc48e91b7814097586ec32c3c7a8965f04cf269bfb'),
('core','function','public.inbox_claim(uuid, text)','b453f57609e135abf1943b8dde5b92ef63490d57fa01bd383baf910bcfd89d2b','927fd1f96e8b3f61ec92cb525820bed5c9da76fe3ec426812380dbd656b10bbe'),
('core','function','public.invite_accept(text, text)','07e92f2d5ed892f54770a5ae2c59c9427de7d6a5c6bad9872e07eae27ab727f8','b7a7b3f9937ac69212fcd51b325c796dc890de9b795a0e052270315bc3079988'),
('core','function','public.invite_accept_id(uuid, text)','ae029c8dce7014d42558c39894de5b7f375e5410c686aacc42a7c2f963e3c27e','d61c6972108e7feafa0a77ab32c436c8ec4971cacedf2c219fd4f7d6dc9e0fa4'),
('core','function','public.invite_create(uuid, text, text)','aab167e020ea9c637cf4f9569e2a76de1bca431c35e458269887a81e46d43b61','a0dbc6c161f012d3407ae8bd6a7fbfdeae5787322330ebd133c4dd557f613878'),
('core','function','public.invite_create_id(uuid, text, text, text, text)','9e3af49f4ba088db2838a07cd8ab488cc586ed658b616bf5a0aa3c26410ba21e','464013225fb2aae4543462f6b4b193bea39f3ae5a79b2dc2552369b9c002af2c'),
('core','function','public.invite_create_open(uuid, text, text, text)','e292b3ae0f6913ae29086e4d237665af5aa38c95f21c963814dca16948f54cdf','f324f3dc4210e458bd3377558aa0b1ea9dcc99f42fc8dad5a0cdffb16b6dfb05'),
('core','function','public.invite_revoke_id(uuid, text)','ae029c8dce7014d42558c39894de5b7f375e5410c686aacc42a7c2f963e3c27e','5591f700d4515938b18bb81d7a0256c34cbc8af59b4714202d957edb8a5cb0f4'),
('core','function','public.member_revoke(uuid, uuid, text)','b21d2619f75fc89123cf62e4712648bb19542343013de3695b5fe3d63c269470','f475233075649d9b9de8ed946eefd6a38420da6568f48231a862586f2d34e65e'),
('core','function','public.message_send(uuid, uuid, uuid, uuid, uuid, text, text, text)','0943ef23fe0ae68e9eedf67d99587d468a493a8ec91f961272ba79b864ec94bc','b8ca6013d3168e8769f3e65454d61371665318f09cdc6f6cfe26ca9e4a14d77c'),
('core','function','public.quorum_agents(uuid)','0186bf1a8cbfa1eb92089d7530d56e256ff02fa1e30f25ee4dbab4cb5ccfa1c2','11373879fade11eb438060ede87aa32c57c82517e4140b593e2ce57abe656c5f'),
('core','function','public.quorum_check_owner(uuid)','61dff2de41a02e421f42b02e0cb2fd5bebd3ce08c3528e0612dbd204386d4060','9d36d03266bea3a3691b4282ad196145158be91f629344f552c08f670da9940a'),
('core','function','public.quorum_create(text, text)','6411745f4a2dfd23f48a081f7f6ad9553f774123848f1f091496f81e1e397eca','7bd57f4e24797a82088637dfc5f1f0390a4ec00e2c4b422220b48a4d252a8a70'),
('core','function','public.quorum_lookup(text)','98e66ddf1059048cf84c9aba82cc67193499d8f8212717f7a90c9ba7323337fc','f5454083089191ebc1a312eb0e76d1954534efa6306ea2bb33b376b04f23bfc1'),
('core','function','public.quorum_set_access(uuid, text, text)','960ed7769ad9e18e7a659d51cb5f966ff457fdb41868693dbcc38600bf36a182','b4f74ea73985d7e2f03560ffdb57b3ef6f117375e3c0c2ce88b072ab0ed8b480'),
('core','function','public.resolve_role(uuid, text, text)','458665b11b4141a0dfead3854af85a12076b09474d7895e125fbcb8d7d792da8','c75b3982fce17a7c32caa7d81f2e74d4bc3aa903b88a54f939ee6849ab68aea1'),
('core','function','public.session_ack(uuid, text, uuid)','9b3d42d5f566d1a8202d65faa0562c2de56fb6bebecda0a9f7d96fb4283eff2f','2ddc386ed6b7cce66088d8e331129a24158853f587c946160cd12dee889f47f7'),
('core','function','public.session_delivery_changes(uuid, text, bigint)','bdffcb975cc9dac1e5106647adb6745c976a560994a3929eba89411047488f4b','4f83d90acf79fa9f3a0b6fe59bee3a4984c8d84a04de707692470bf8ee7866e7'),
('core','function','public.session_delivery_mark(uuid, text, uuid, text, text, text)','2a4199f20c5511467693173d7725f87c12114c6de130e0a4843239b7ef77b1be','9eb3e8f3b332e86a940c1a235f8a68f1e19b57648aed865df53d2e8ad2afd55a'),
('core','function','public.session_delivery_status(uuid, text, uuid)','9b3d42d5f566d1a8202d65faa0562c2de56fb6bebecda0a9f7d96fb4283eff2f','688a43db8014b0df0c7cf8c24ac0ae2e504cc124f0d05afbaa62bd337c56a710'),
('core','function','public.session_delivery_stuck(uuid, text)','fda3bdf93ac4350092239051b6b9396555d79a32e44dd6273f748b9cf16dd908','9856d916544f75e032c7e65aaed1c05c0412bdd0cd7e3bc9902e4769885b5a78'),
('core','function','public.session_heartbeat(uuid, text, uuid, integer, text, integer, timestamp with time zone, uuid, timestamp with time zone)','e500e4bf975ee66d7da8934692b6fb1c2a3e6627ad14100e42f3fe200ac4ebe4','9216a8af40e7bc1f8cd47e771947123e111c1e6e609ff26143aa9bd7875f1776'),
('core','function','public.session_inbox(uuid, text)','fda3bdf93ac4350092239051b6b9396555d79a32e44dd6273f748b9cf16dd908','6afd3683f89297752a12ce087e917c096193dfd4a7e6be79b6f3bf3dd8ce7215'),
('core','function','public.session_message(uuid, text, uuid)','9b3d42d5f566d1a8202d65faa0562c2de56fb6bebecda0a9f7d96fb4283eff2f','d7bb366d08504d440cbdf64342f171f2ee8783277bd695438d5d087b0f51189e'),
('core','function','public.session_message_unreceipted(uuid, text, uuid)','9b3d42d5f566d1a8202d65faa0562c2de56fb6bebecda0a9f7d96fb4283eff2f','4fe9673d606ce747e33afc163d62e569101b3c7e95ee80bbca34bac83cc78c02'),
('core','function','public.session_peers(uuid, text)','fda3bdf93ac4350092239051b6b9396555d79a32e44dd6273f748b9cf16dd908','ef48680d6d00fa2d74182daefb6b96caa38e206ea23c8b68ca7f83fa2b1d2f77'),
('core','function','public.session_release_report(uuid, text, text, uuid)','b3011eb06b7d9632243bbb66a67b433349382a2025e4a4bdadb2f748a38c2bf5','2b879ffd5d66188008d7f434dd4bbb34dc0d3c873c844afa08f8a5b964177619'),
('core','function','public.session_release_status(uuid, text, uuid, uuid)','1bfce71f6c5cdd9d2423c848d3f9871801ef57a5641988cc20039e63fae422f4','663fcb9c793282b031b30d84b1169a2089df0c371899913e3ee4048e4d485796'),
('core','function','public.session_reply(uuid, text, uuid, text, text)','53be7ef5e8965115be57bf545892ab532285b40a7fd4ab3ee93047407799c152','f10c1f329a50c1f19217603b8f02221f58b943e14aa0607a04da53ed9797fac2'),
('core','function','public.session_requirements()','8fe1053fd27a6dc0aca3dffd8b32254fc7214747e55c4db5dc59beff149ff916','e276e4392bc8b8782eb4e5f4ff9b14eb9a82aed803082edbf778ea36de523b8a'),
('core','function','public.session_task_authority(uuid, text, uuid)','9b3d42d5f566d1a8202d65faa0562c2de56fb6bebecda0a9f7d96fb4283eff2f','5d0c0bdfdff7083906dad012f02ee4ef36f672f6e2c3fc822cea452115f2965b'),
('core','function','public.session_update_report(uuid, text, uuid, uuid, text, jsonb, text)','0a6fc6ea51ca2b135e9e1d7c137f3702d602263c6044be15cadf3f2b0bb1cbfb','7db1fb3388c741dee42e398d5b6149184116402bf6523f6e275831a5fd0b4c58'),
('core','function','public.session_update_request(uuid, text, uuid, integer, text, text, text)','2cb3332df238e9d416a1ed695998384c4d639870d7f98f11e55fc62b60d6a5bc','bd9aaba7d04609d1750a567c419a599538a6ff16e4cea8fe79214ff49b86cb23'),
('core','function','public.session_update_status(uuid, text, uuid, uuid, uuid, uuid)','67ebc6e0d9e9ae117fc44ebaa7982607532b99368c4df9b801267be9254b73a0','33e5ec2534fb4bb948abdb6f5b853365221b2ecdbf1e33379926912120418eef'),
('core','function','public.work_admit(uuid, uuid, uuid, text)','c9dd7cbe1150f3f68240d9fd49f5aa0043202edc112f614ccb1db0b1333ab649','b7d8a0d2b27dd94ae61737aaa0edcacf3f05d7b48f19e7bd962e7201e74b348a'),
('core','function','public.work_register(uuid, text, bigint, uuid, text, jsonb, text)','840d84d2563ba4be05e340035c0f3b07d6baac82ad9d72081c541cd6c75b2feb','dd80662b95138aa0b4b8fda461ce4292589fb8b7eb52277bc7a04e9ce3bb9546'),
('core','function','public.work_transition(uuid, bigint, text, text, text)','cb92aa2f5b73d38ecd655e09d5fe258f27c7ea76173a51b94e771b869dc57850','12635e61cfe406a3aa6da0b8058bb36ae7b33dea2203adb29042c3b75dbb1040'),
('core','function','quorum_private.call(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','c7e71c9dcc515b6f0eb1f5b64b0e4cb431c94422b1bb763fc2687f5c2b86c99a'),
('core','function','quorum_private.call_invite_core(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','f6a10b1b7f476328eaa21e34bff6336bd734a1dd1205684bdd6f0d5ea814aadc'),
('core','function','quorum_private.call_legacy_core(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','1aa452947be35e79c190d3f59baa8a3ffc8da8ffdb5eb36d07bb2fea23a4bebe'),
('core','function','quorum_private.call_priority_core(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','01f0fb843a904ff3b615e432ab054c25e44d3c0494e5213526ddb232e04b512c'),
('core','function','quorum_private.call_role_core(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','ca718afbcda8682ade19ea6b246ccc2f1bfd5523eb7d1076cf4e9583e24f3ab7'),
('core','function','quorum_private.call_serial_core(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','ac169502144c0d19620bc8a18a9e89f5990367053b5157982252f53a550b8934'),
('core','function','quorum_private.delivery_status_answered()','8a4cae0308224f5d2d0e25e4f8417f7c460176b353abe085aca5d131b9dfdfcb','7e8e2d7b8196137a53a19a27a2b7f2de7bc979c93491c185bcb53112749e4c3c'),
('core','function','quorum_private.delivery_status_applies(uuid)','0c97e122547e9a82bb6e1d5d9badec8ddcc784cfa0e8b770c128f1c21d464410','c664291ffebef1b81c1bc44eccab3e4ba3b6b08716c789e7329ef623ed62a4b6'),
('core','function','quorum_private.delivery_status_apply(quorum_private.messages, text, text, text)','a68cc7f078246fe94ceaea3221ab34ddce27ebaeb6cae135b5c5155ead2d17da','c2c23c46728237eed47ca7979b5b210eb710b31432e33a463400cc223b539be2'),
('core','function','quorum_private.delivery_status_view(uuid)','afd6309ff479422efe00494e7bba873cacdede512ed6ef9ef7dc83aa0cdaac32','3c2d98b0382b2d8345b463c001594dd1db5fa160d3a4e42e337ced4692f1ce95'),
('core','function','quorum_private.member(uuid, uuid)','667598ad6ec589ab24b3c0e469d988834e9fc70042ed86b692c62292884a3925','155cf2dcc2b2d61c5ff55a7533b13eae931b9e077420f3aaf2408b7a22ca7435'),
('core','function','quorum_private.native_session(uuid, text)','ed8a28541c9d69d631b44e7413f88c15ce7bdc8bf4823e12cc14869e52174814','60778eaf4fde7ce50980d5bf328f21c85d9db23f0947ed1554e0052a8654615c'),
('core','function','quorum_private.notify_inbox()','8a4cae0308224f5d2d0e25e4f8417f7c460176b353abe085aca5d131b9dfdfcb','ce20a6fdf38e633676cb5e60f1c59c5fe96943d86a9b1f669fb83742cf8c9f8c'),
('core','function','quorum_private.open_invitation_ttl()','20dfc4277760817d27d80827d926172bbabea1a1965ccc826f39f0f00abaa5c9','09b1e1bd29b246ae23176212c7353857179bda883b930b6cac9c458c25415569'),
('core','function','quorum_private.owned_agent(uuid, uuid)','708cc9d92fdd73a71998fc87b775d89735c4f763ba042505b3ec09e472a8c4d3','9129479e881a5c3c2e2e6519ebdae2a15bece805444791ba12aedc1b750a3479'),
('core','function','quorum_private.receive_wakeup(text)','3e7936b06502a0b403095e563f8d9125666d93619b5c0658073b04af07eb830d','2f82e7bad7cedc81609fab91f8b4d680fe40a71216ce89c90fa66317ac24eb64'),
('core','function','quorum_private.session_ack_before_updates(uuid, text, uuid)','b9471120ee18ae2218826e76b70c8cede5df619e323a58ff6efd02cbddfa606c','122d58111a0e99120a547c2ccc48fd6c3589c193b52a30ad884f1e89c5c0585c'),
('core','function','quorum_private.session_health_streak()','8a4cae0308224f5d2d0e25e4f8417f7c460176b353abe085aca5d131b9dfdfcb','8881904b8a68daff482f04eb034f32a4e1b8a39649628d5e4559ef0a13ce7032'),
('core','function','quorum_private.session_heartbeat_before_hash_only(uuid, text, uuid, integer, text, integer, timestamp with time zone, uuid, timestamp with time zone)','817747b7f8e1548d8611ea532dcdf992df049451f93a765dbb0e6c68bda47019','2957604dce5af878d0da3713b17545e11a745a95c4fc1aad95c222f6aa5394a2'),
('core','function','quorum_private.session_heartbeat_before_hello(uuid, text, uuid, integer, text)','1f78ef9ca05c5dff795777227f3899198e1cfa94b224e84103e7dd5bba4c7b28','b3791a5b3b5a93433a6a3dd854af5ffed93e9843bc905b811e201b20d55873a5'),
('core','function','quorum_private.session_heartbeat_before_presence(uuid, text, uuid, integer, text, integer, timestamp with time zone, uuid, timestamp with time zone)','3056e40014c467b3fa30c1aaa184ad885d629b67cccee4774feb1feef3066f54','019b882f0824fe1ff0491bcb3c3f0d80171eedf2a7d16c1d116d4e6a5d1ed725'),
('core','function','quorum_private.session_heartbeat_before_status(uuid, text, uuid, integer, text)','1f78ef9ca05c5dff795777227f3899198e1cfa94b224e84103e7dd5bba4c7b28','be24c31bc064a0f4dc70bb4d98086525a49738e27749bb1eb53aa5ce40dc6c76'),
('core','function','quorum_private.session_heartbeat_core_before_dedupe(uuid, text, uuid, integer, text)','1f78ef9ca05c5dff795777227f3899198e1cfa94b224e84103e7dd5bba4c7b28','944211db94640dca91d75e007a4c5de8a1ee28511ea6c78a5b3fb2f7d96768c5'),
('core','function','quorum_private.session_heartbeat_hello_before_dedupe(uuid, text, uuid, integer, text)','1f78ef9ca05c5dff795777227f3899198e1cfa94b224e84103e7dd5bba4c7b28','3fa1dc12649d4b162720b0cc5af8ada060a4b30ea95095087c83eadd15ad8e41'),
('core','function','quorum_private.session_heartbeat_hello_before_split(uuid, text, uuid, integer, text)','1f78ef9ca05c5dff795777227f3899198e1cfa94b224e84103e7dd5bba4c7b28','2feca63b79981667839a2c93d3c49728b9c135f39de97ee968025bd9eb183a5f'),
('core','function','quorum_private.session_inbox_before_hash_only(uuid, text)','86c9311a7c12f028b0b7b86c6649b4da571c8901b5cbf2907f1d3ffaa980273f','19b0ea41c4c595df85f71dd57e25daddc2fd3bb3343aad49aa3b82b29031bfe6'),
('core','function','quorum_private.session_inbox_before_updates(uuid, text)','86c9311a7c12f028b0b7b86c6649b4da571c8901b5cbf2907f1d3ffaa980273f','93b2f7f232bc3b318114192dac7a597bf8ab700cd0a4158daf081f94337f3e79'),
('core','function','quorum_private.session_peers_before_hash_only(uuid, text)','86c9311a7c12f028b0b7b86c6649b4da571c8901b5cbf2907f1d3ffaa980273f','7379f998ad449d00d1078eebad6e98876f5d950d1faa9d36d97b9e3c96a5438a'),
('core','function','quorum_private.session_peers_before_status(uuid, text)','86c9311a7c12f028b0b7b86c6649b4da571c8901b5cbf2907f1d3ffaa980273f','e6bef8353f48cb6322f76dcb024eeb4972d2881eeb65b28c62023e2d2f8b31cd'),
('core','function','quorum_private.session_task_authority_before_project_bound(uuid, text, uuid)','b9471120ee18ae2218826e76b70c8cede5df619e323a58ff6efd02cbddfa606c','4366ec8288290632601454a8eccf39fccbd20cb7264ee07c5dca42118f724e09'),
('core','function','quorum_private.session_update_report_before_hash_only(uuid, text, uuid, uuid, text, jsonb, text)','f1678acc6033f0581d09ab92db547c23b64a079030ac7cb9e991502d049720a2','abce7c4012a3221f7ddafebb202c6468ed0bf90e5795f33babdfbb4a3af68060'),
('core','function','quorum_private.session_update_request_before_hash_only(uuid, text, uuid, integer, text, text, text)','c793f994a021fb224413fa3923d2eb4ac61b6fb8f707284e13404e8920a14c89','07fbf7a1bb01ea3d1ee575750381a52cea638989cbf058bcdf6655de605dc5a1'),
('core','function','quorum_private.session_update_status_before_hash_only(uuid, text, uuid, uuid, uuid, uuid)','d40e199406455c4500c1e04cfa43f00d1f06339304ae3ee8ca8a9127d1a33138','cf0cc30367424e8e731df31a88a3740db846809e37047136d3c911af22182fef'),
('core','function','quorum_private.update_session_locked(uuid, text, uuid)','383d6d8abed63a65de75dfa2e0af091604d5c08ab99f9f8130503cad6fd85c86','106b03f6b972dfa68e6a1bf088a3741f2721e2036fa1b655085fb5290ca19a12'),
('core','function','quorum_private.verified_actor(uuid)','6859c2c958fa545217f615495e31112d5d654b46af1d3844e6ba2a9fbd7332fa','f20291c082af61a232754c29480b65877d7994c177c06ce079b655af6b0f832b'),
('core','policy','realtime.messages.quorum_agent_wake_receive','52de95d9977f62b3be3aa06350401aa60d0d486608aca42cd4bbf2c46e042697',NULL),
('core','relation','quorum_private.agent_supersessions','7dca4e885c7373b2455488cc89f57f4e4e862fc9e64aafef6ebf0a45ea26538f',NULL),
('core','relation','quorum_private.agents','07e764ea709d8f04a52eb7639259222e42e1275dc33fbb8b789879cda2040fa7',NULL),
('core','relation','quorum_private.attempts','62e7986971c63425248adce808c29276b10b746665ad591f0187b5f4818e0087',NULL),
('core','relation','quorum_private.audit_events','980aa6d7d23371b6a81fd4e304d98b078acc8e51c18190fd9435f73e2ab90388',NULL),
('core','relation','quorum_private.audit_events_id_seq','348cfb180655e27e14145b500be96d07628a4256ce89d3c1b655d6ba78d1f025',NULL),
('core','relation','quorum_private.capacity_reservations','e932a7748b9243fc09ff42e8021142c27579193e3055833e635463557bac135d',NULL),
('core','relation','quorum_private.context_versions','7e5f90594a8a3d1bbbaac53d5221ae0f089e7fa4e23140a393f7e9a68967b744',NULL),
('core','relation','quorum_private.context_versions_id_seq','348cfb180655e27e14145b500be96d07628a4256ce89d3c1b655d6ba78d1f025',NULL),
('core','relation','quorum_private.conversations','2a2f1eb7c37dc0c46a955c80761b50381741079a41b1c40aa0acbd504040ef18',NULL),
('core','relation','quorum_private.deliveries','8f06463d5176c24f0d01d1aed68ec8c2751eeae5f05ce7eef4982a89c66d511a',NULL),
('core','relation','quorum_private.delivery_status_streams','50ad1269dcc264149e51e3fd868856b87c649e3cffcf4aeb135dbf1901204fce',NULL),
('core','relation','quorum_private.delivery_statuses','b6640de0baf149b1c95afd1b55513edae1b77941d3d0d4bfbf98891f4fd5903c',NULL),
('core','relation','quorum_private.devices','0130f2b3aaed496b68081ac05f41ceb478fa2397d4e7e76322f468f64c151b8f',NULL),
('core','relation','quorum_private.id_invitations','3d76b977d9e4434f52529bbeb1500979e46c7b3638e5d7981b898d57d2d7c49f',NULL),
('core','relation','quorum_private.instance_creators','e6a4748302e32bb25e9de8b8800446ef50ab9db2188ba3fe496d57d20c29db2d',NULL),
('core','relation','quorum_private.invitations','21392efe42d38f893700254d60b8b399177ac40873206d39786dfb0923ff378c',NULL),
('core','relation','quorum_private.memberships','4aacbc866a33a8d25ad297928b3cbfa50bb2f582730d866a30287d3077712ef2',NULL),
('core','relation','quorum_private.messages','789dba796e1e58e89638e6c3b1dc4385e304cb8b56558e052c7ff6d75354c97f',NULL),
('core','relation','quorum_private.quorums','7b2797a17146265353340d89f78d60bf4c1cd78a04d311fda065f690f7f66958',NULL),
('core','relation','quorum_private.requests','eeacf11b64ea6209f83dcb4c864c169db032f4539bffd75fe4266892cdb64730',NULL),
('core','relation','quorum_private.session_connections','667a161f156834aeba4f67d0bd6ab1a80fe911e37f8af610a0273dec5942519b',NULL),
('core','relation','quorum_private.session_health','8f5e6abd33d03ca8b8bdbede7dc596d36f94c1c7bee4557e0a08071e359c12e6',NULL),
('core','relation','quorum_private.session_hello_sent','1448cf2998b3fde71e6b590b324eccdc93a64bfa86fe301f9fd0ef00abba3d18',NULL),
('core','relation','quorum_private.session_hellos','f784f7eaeac5472c78ecd801599907c60403ac05077d7452ea2faf932c7b9c51',NULL),
('core','relation','quorum_private.session_notices','7fb4f79f1c81264605caf0343f6d96c9f8aba4539b3a30d30d147b7a3d7ae04b',NULL),
('core','relation','quorum_private.session_receipts','5ba894cb1fb2e2ac5f0637bbd572dab1408c649ae6932a93d0c267a5a9e05854',NULL),
('core','relation','quorum_private.session_release','57c147fc24043b72509fce869a7bd53bce55705aa3dff41a46ccef055ab547c1',NULL),
('core','relation','quorum_private.session_status','f950c22ef614e418aaf29a5eb4af7f6c3e2f3e47a93d1ce1cce88c1615bf7950',NULL),
('core','relation','quorum_private.session_streaks','8643ed0f8cdc46f47f5fd97c69191d4f7d54c448223b9a09dea02ce556974387',NULL),
('core','relation','quorum_private.update_notices','76cc055dfaa5465d8c96e70ecbc2c1219e3ed52c797b371e5e31714b891a4cdd',NULL),
('core','relation','quorum_private.update_operations','0b67fd236441e32cd946d0e868ff509f518c2e2de660ebd7eb97f911e3397e21',NULL),
('core','relation','quorum_private.update_recipients','4b09afa92271928e4411c2e6d7f48f59a57645a5f8b88e67ab8c95f65c776871',NULL),
('core','relation','quorum_private.update_reports','b1f018d5b52907ec12167d84bdd3a049e97f8ca6a9ab4c76b2a6d8a5f378e00c',NULL),
('core','relation','quorum_private.update_targets','decbc81f5e2074831f3b3e2925b9ea356b73cbe034a17d2d4b68533137246a38',NULL),
('core','relation','quorum_private.work_items','8ef4ebcf79888f07a0f575e5392582f3ec81b003e3a0a51ad1751a5c9fe8da07',NULL),
('core','schema','quorum_private','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',NULL),
('knowledge','extension','vector','20ef7739e8423f114899b767909a220c5dc4169d73a38196d7a2dbfac5166c4e',NULL),
('knowledge','function','public.knowledge_admin(uuid, text, text, jsonb, text)','7ada605989d9321a3732a727f9f4f2776b04e1ffbdec215587b5ed097bf64612','99aae926ebb281a399703a1da728b828b2526b725121cd97b051f99a579b3656'),
('knowledge','function','public.knowledge_catalog(uuid, text, uuid, uuid, integer, integer)','c10c7fa5ee7724036a7b21ea48f1b3f9ecec84772bcd49235c6c2b09b127b769','e4d040099c4875cb948e8f2a7ea3f226ac7c5d6cd8c7bb775ec4e099d7ec02f8'),
('knowledge','function','public.knowledge_index(uuid, text, uuid, uuid, integer, text, text, integer, jsonb, text)','b609ef2bb47d1b1b7a6a56e2ae04b5e2254fa863d682bab53159e987c798c330','7a768927edae4f831439daf5dd05c34a044a1a625357e68806b0d1bccd4f6b1a'),
('knowledge','function','public.knowledge_read(uuid, text, uuid, uuid, integer, integer, integer, boolean, boolean)','ce135f4fcb584e7c34a1afa22b0f29aae08ddb2a416c0fa28f9dee0ef82f24df','03b4b19732a394c8582b1e525b1bd4a16228385dfb1a9518bb33dd35c2071e69'),
('knowledge','function','public.knowledge_search(uuid, text, uuid, text, integer, integer, text, jsonb)','610c699317928ae6d84f8c42f431773852dadd16fef5b3d5f204d98644b20fb0','2db3b7d3f85719813be974fe82f60c08e4c1b71a557e268d2c14145a44428ea1'),
('knowledge','function','public.knowledge_source_read(uuid, text, uuid, uuid, integer, integer, boolean)','c42eb9a1e0ad3b3ab111e30604be908c3aaf39dcf61bdf3fe5a9040d2499da20','6e81e61cd81d5c866f5aba210d88d0635d9e9ceb5ebd03c225782e6350a2e924'),
('knowledge','function','public.knowledge_source_write(uuid, text, uuid, jsonb, text)','dbad8007ea65be07c1c44cfa465d6be78c831082faebc8705734c442db344996','4a242c00a0791039b59bd000105330ee84f0bcf1bde93b7169c445cd772f3819'),
('knowledge','function','public.knowledge_write(uuid, text, uuid, text, jsonb, text)','384b99d80faac34523378a11d4d631585f0256894fcdeca025beb48de81d12ee','d20b67540dfc37083591f2df7a21e326d08758fca4a19dd3c5783b4ef9590f5d'),
('knowledge','function','quorum_private.knowledge_admin_before_default_role(uuid, text, text, jsonb, text)','78bd91c29ac11173dd9ba8407d6ede34b9e9d5e7171ab8298364e79291d50a69','29d16c63750d499c998f60525e0c79f0a4d371d2e4ccaabfbe5dbca7b1c840f1'),
('knowledge','function','quorum_private.knowledge_binding(uuid, text)','e23c33fb63b077ad48bc94038f8089a26a17ddc93d4751c44cdfca4f55b57f26','e42a7dfb2b37f4f4261a26bcc51556cb8bee34bf00bdf7209d9e413cda0145a6'),
('knowledge','function','quorum_private.knowledge_cached(text, jsonb, text)','670dc8adc03d383dd94a96c20057c1b75fbb379991021d66705b5d884b8e6bee','ab66b04077746fcb9bed8e3d6eb755399fd12aa0bf3ce086321271e42a873d53'),
('knowledge','function','quorum_private.knowledge_fields(jsonb, text[], text[])','f8409c16a25827c2b2a03084798528b57b85a68d02b4411ba012f0a0c141ca87','f5c677c4f2e0dbfa21864e6fea6ebf41e1c10a9ae695b3fe25d253fdfd59d80b'),
('knowledge','function','quorum_private.knowledge_immutable()','20dfc4277760817d27d80827d926172bbabea1a1965ccc826f39f0f00abaa5c9','cec9202c86cbddc3233786b5761c0d614c07b125bece3077d55635df72979659'),
('knowledge','function','quorum_private.knowledge_mutation_lock(uuid)','75cd528a376f6292ef5023002e0704d233db773007f29e536613efec8eb21c8b','21079f9e957ea7825d4ca763aa999bfa4a56142aea6b1a7e4ef96b2e5a038047'),
('knowledge','function','quorum_private.knowledge_projection(quorum_private.knowledge_revisions)','2fb74b0c5bfa33e81b63b551349ca1f284d807aca78d74645f4bcdb86a384054','370eb5cf876ba3618930566d7c43d2fc63bf55b8c5198f23d4f3478f5e7acd9e'),
('knowledge','function','quorum_private.knowledge_save(text, jsonb, text, jsonb)','fb2c3926494c1f63de44a29e0699695bbe62eef2a62fef6956d9744787fa3301','8679767b3e13ae5fc2c2dc259f04d404a11d373db7277148181e636f914174d9'),
('knowledge','function','quorum_private.knowledge_scope(uuid, text, uuid, boolean, boolean)','8bb536a87e167e4b24046754d962155a06521b157dab6a8b449637dc8fcf8279','47650d0021750721de811a33e75b5f48f6db4202494b16fe325c021a4f5817ec'),
('knowledge','function','quorum_private.knowledge_scope_before_default_role(uuid, text, uuid, boolean, boolean)','8bb536a87e167e4b24046754d962155a06521b157dab6a8b449637dc8fcf8279','5879b35c9d58f0885dd8838b3956077469922bc4b1f30216c8c5dfb883473e2c'),
('knowledge','function','quorum_private.knowledge_uuid_links(jsonb)','b5a02dceb6e79e93cac1a1726f00ca9176aa7acd2a51b7af72a7fbfcfad39a34','bae5ebe50867f0954009c0b9bbac12499b5ef026a4d7417ddf6d2bdddc596bd2'),
('knowledge','function','quorum_private.knowledge_vector(jsonb, integer)','785e03dbfc1d830bcbdd77f9f2b2c29b1a164b7591a4e625a84a1f9049452869','a98f3352fc6b70a130ccdcc1ed454599fcfd88b0bb9f40e90a06b67f4fd9549b'),
('knowledge','function','quorum_private.knowledge_visible(uuid, uuid, integer)','d2770aa7a595993fb2a01cbf0cc4ca86107204ebd099c12b38ee11ff8ae53ed0','4a3e10a80a5ea341a3545d5f94a83a6de1be2075db4f39e12b6cd70ab33228ad'),
('knowledge','relation','quorum_private.knowledge_attachments','edb85b326628515b44064622853119cb4cf1fedcf7c4aa0296a20a53b8689a74',NULL),
('knowledge','relation','quorum_private.knowledge_entries','cdc2f9dbb5def30ddb5c1a8d5ed78c65da16f6de1f3baa94463e9c615a147a9d',NULL),
('knowledge','relation','quorum_private.knowledge_grants','71d66c556a7fc88c45b5cfd5645b8ffb9220c494a71298ea32d21a44dad6b933',NULL),
('knowledge','relation','quorum_private.knowledge_member_defaults','6a2c98508bf0d65ce3f07f9403c2bce07968446c0cc9ff96d56c17130b22e43a',NULL),
('knowledge','relation','quorum_private.knowledge_page_tombstones','a5af6c856daa1fae1733fe2f9b1253ee26e847e41459b3ef4603b08fffa377a2',NULL),
('knowledge','relation','quorum_private.knowledge_requests','72e9adca8e851de8061d5bd317b2cd7dbe45482dd20f94aa0b52869675ffa529',NULL),
('knowledge','relation','quorum_private.knowledge_revisions','8117cd79685be9325591786b03b03e0047b9df7e1f48a2ae31b4374e79624c5f',NULL),
('knowledge','relation','quorum_private.knowledge_source_tombstones','5af57e7fac73971aa2e504fda805001402918f48f7d403352e143700d63d810e',NULL),
('knowledge','relation','quorum_private.knowledge_sources','e0d170abd50cf2355256a84fb0b3c46396e7f1ca8ed431b31018da9bffc0ac81',NULL),
('knowledge','relation','quorum_private.knowledge_spaces','791c4edd1c08c24eee6971649aebeeca60d194687025f38661a054c53365249e',NULL),
('knowledge','relation','quorum_private.knowledge_vectors','8967bc5b492ab0ebcfaff5f23856a1037c79baeab8cf85f680d5e7f619f5b0ac',NULL)
),
 actual AS (select 'schema'::text as kind, n.nspname::text as identity,
  coalesce((select pg_catalog.string_agg(g,',' order by g) from (select case when x.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(x.grantee) end||'='||x.privilege_type as g from pg_catalog.aclexplode(coalesce(n.nspacl,pg_catalog.acldefault('n'::"char",n.nspowner))) x where x.grantee<>n.nspowner) y),'') as definition, null::text as prosrc
 from pg_catalog.pg_namespace n where n.nspname='quorum_private'
union all
select 'extension', e.extname::text, n.nspname::text, null
 from pg_catalog.pg_extension e join pg_catalog.pg_namespace n on n.oid=e.extnamespace
 where e.extname='pgcrypto'
  or (e.extname='vector' and pg_catalog.to_regclass('quorum_private.knowledge_spaces') is not null)
union all
select 'relation', n.nspname||'.'||c.relname, pg_catalog.concat_ws(E'\n',
  c.relkind::text||' rls='||c.relrowsecurity::text||' force='||c.relforcerowsecurity::text
   ||' acl='||coalesce((select pg_catalog.string_agg(g,',' order by g) from (select case when x.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(x.grantee) end||'='||x.privilege_type as g from pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault(case c.relkind when 'S' then 's'::"char" else 'r'::"char" end,c.relowner))) x where x.grantee<>c.relowner) y),''),
  (select pg_catalog.string_agg(a.attname||' '||pg_catalog.format_type(a.atttypid,a.atttypmod)
     ||case when a.attnotnull then ' not null' else '' end
     ||coalesce(' default '||pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')
     ||case when a.attidentity<>'' then ' identity '||a.attidentity::text else '' end
     ||case when a.attgenerated<>'' then ' generated '||a.attgenerated::text else '' end
     ||coalesce(' acl='||nullif(coalesce((select pg_catalog.string_agg(g,',' order by g) from (select case when x.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(x.grantee) end||'='||x.privilege_type as g from pg_catalog.aclexplode(coalesce(a.attacl,pg_catalog.acldefault('c'::"char",c.relowner))) x where x.grantee<>c.relowner) y),''),''),''),
     ', ' order by a.attnum)
    from pg_catalog.pg_attribute a left join pg_catalog.pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
    where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
  (select pg_catalog.string_agg(o.conname||' '||pg_catalog.pg_get_constraintdef(o.oid),E'\n' order by o.conname)
    from pg_catalog.pg_constraint o where o.conrelid=c.oid),
  (select pg_catalog.string_agg(pg_catalog.pg_get_indexdef(i.indexrelid),E'\n'
     order by pg_catalog.pg_get_indexdef(i.indexrelid))
    from pg_catalog.pg_index i where i.indrelid=c.oid),
  (select pg_catalog.string_agg(pg_catalog.pg_get_triggerdef(t.oid),E'\n' order by t.tgname)
    from pg_catalog.pg_trigger t where t.tgrelid=c.oid and not t.tgisinternal),
  (select pg_catalog.string_agg(p.polname::text||' '||p.polcmd::text||' '||p.polpermissive::text||' to '||(select pg_catalog.string_agg(g,',' order by g) from (select case when r=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(r) end as g from pg_catalog.unnest(p.polroles) r) y)||coalesce(' using '||pg_catalog.pg_get_expr(p.polqual,p.polrelid),'')||coalesce(' check '||pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid),''),E'\n' order by p.polname) from pg_catalog.pg_policy p where p.polrelid=c.oid),
  case when c.relkind in ('v','m') then pg_catalog.pg_get_viewdef(c.oid) end,
  (select pg_catalog.concat_ws(' ',pg_catalog.format_type(s.seqtypid,null),s.seqstart,s.seqincrement,s.seqmin,
     s.seqmax,s.seqcache,s.seqcycle) from pg_catalog.pg_sequence s where s.seqrelid=c.oid)), null
 from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
 where n.nspname='quorum_private' and c.relkind in ('r','p','v','m','S','f')
union all
select 'policy', n.nspname||'.'||c.relname||'.'||p.polname, p.polname::text||' '||p.polcmd::text||' '||p.polpermissive::text||' to '||(select pg_catalog.string_agg(g,',' order by g) from (select case when r=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(r) end as g from pg_catalog.unnest(p.polroles) r) y)||coalesce(' using '||pg_catalog.pg_get_expr(p.polqual,p.polrelid),'')||coalesce(' check '||pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid),''), null
 from pg_catalog.pg_policy p join pg_catalog.pg_class c on c.oid=p.polrelid
 join pg_catalog.pg_namespace n on n.oid=c.relnamespace
 where n.nspname<>'quorum_private' and p.polname like 'quorum\_%'
union all
select 'type', n.nspname||'.'||t.typname, t.typtype::text||' '||coalesce((select pg_catalog.string_agg(e.enumlabel,','
   order by e.enumsortorder) from pg_catalog.pg_enum e where e.enumtypid=t.oid),'')
   ||coalesce(' '||pg_catalog.format_type(t.typbasetype,t.typtypmod),''), null
 from pg_catalog.pg_type t join pg_catalog.pg_namespace n on n.oid=t.typnamespace
 where n.nspname='quorum_private' and t.typtype in ('e','d','r','m')
union all
select 'function', n.nspname||'.'||p.proname||'('||pg_catalog.oidvectortypes(p.proargtypes)||')',
  pg_catalog.concat_ws(' | ',p.prokind::text,l.lanname::text,'definer='||p.prosecdef::text,p.provolatile::text,
   'strict='||p.proisstrict::text,p.proparallel::text,pg_catalog.pg_get_function_arguments(p.oid),
   pg_catalog.pg_get_function_result(p.oid),'config='||coalesce(pg_catalog.array_to_string(p.proconfig,','),''),
   'acl='||coalesce((select pg_catalog.string_agg(g,',' order by g) from (select case when x.grantee=0 then 'PUBLIC' else pg_catalog.pg_get_userbyid(x.grantee) end||'='||x.privilege_type as g from pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f'::"char",p.proowner))) x where x.grantee<>p.proowner) y),'')),
  pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
 from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
 join pg_catalog.pg_language l on l.oid=p.prolang
 where (n.nspname='quorum_private' or (n.nspname='public' and p.prosrc like '%quorum\_private%'))
  and not exists(select 1 from pg_catalog.pg_depend x where x.classid='pg_catalog.pg_proc'::pg_catalog.regclass
                 and x.objid=p.oid and x.deptype='e')
 ),
 wanted AS (SELECT * FROM expected WHERE part='core' OR knowledge)
 SELECT pg_catalog.array_agg(problem ORDER BY problem) INTO problems FROM (
  SELECT CASE WHEN a.identity IS NULL THEN 'missing '
   WHEN w.identity IS NULL THEN 'unexpected '
   WHEN w.prosrc IS DISTINCT FROM a.prosrc THEN 'prosrc '
   ELSE 'definition ' END||coalesce(w.kind,a.kind)||' '||coalesce(w.identity,a.identity) AS problem
  FROM wanted w FULL JOIN (SELECT kind,identity,pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(coalesce(definition,''),'UTF8')),'hex') AS definition,prosrc FROM actual) a
   ON a.kind=w.kind AND a.identity=w.identity
  WHERE a.identity IS NULL OR w.identity IS NULL OR a.definition<>w.definition OR a.prosrc IS DISTINCT FROM w.prosrc
 ) p;
 IF problems IS NOT NULL THEN
  RAISE EXCEPTION 'quorum_baseline_mismatch: % differences', pg_catalog.cardinality(problems)
   USING DETAIL=pg_catalog.array_to_string(problems, E'\n');
 END IF;
END $adopt$;

create table quorum_private.schema_baseline (
 part text primary key check (part in ('core','knowledge')),
 covers_through text not null,
 installed_by text not null check (installed_by in ('baseline','adopt')),
 recorded_at timestamp with time zone not null default now()
);
alter table quorum_private.schema_baseline enable row level security;
revoke all on quorum_private.schema_baseline from public,anon,authenticated,service_role;
insert into quorum_private.schema_baseline(part,covers_through,installed_by)
 select 'core','20261108140000','adopt'
 union all select 'knowledge','20261108140000','adopt'
  where pg_catalog.to_regclass('quorum_private.knowledge_spaces') is not null;
