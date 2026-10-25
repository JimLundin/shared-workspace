/// This is the public engine contract relied on by the durable owner's STRICT schema.
#[tokio::test]
async fn actual_turso_strict_table_rejects_a_text_value_in_an_integer_column() {
    let database = turso::Builder::new_local(":memory:").build().await.unwrap();
    let connection = database.connect().unwrap();
    connection
        .execute(
            "CREATE TABLE typed_values (id INTEGER PRIMARY KEY, value INTEGER NOT NULL) STRICT",
            (),
        )
        .await
        .unwrap();
    assert!(
        connection
            .execute(
                "INSERT INTO typed_values(id,value) VALUES(1,'not an integer')",
                ()
            )
            .await
            .is_err()
    );
    connection
        .execute("INSERT INTO typed_values(id,value) VALUES(2,42)", ())
        .await
        .unwrap();
    let mut rows = connection
        .query("SELECT value FROM typed_values WHERE id=2", ())
        .await
        .unwrap();
    assert_eq!(
        rows.next().await.unwrap().unwrap().get::<i64>(0).unwrap(),
        42
    );
}
