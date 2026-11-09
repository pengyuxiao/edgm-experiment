/* UTF-8 位置检查：只改变 typedef 的一条依赖边。 */
struct Payload { int value; };
struct DifferentPayload { int value; };
typedef struct Payload Stable;
typedef Stable ArgumentBase;
typedef ArgumentBase Argument;
typedef int (*Callback)(Stable);
struct Dispatch { Callback invoke; };
static int consume(Stable input) { return input.value; }
int main(void) {
    struct Dispatch dispatch = { consume };
    Argument value = { 0 };
    return dispatch.invoke(value);
}
